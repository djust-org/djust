/**
 * djust Homepage JavaScript
 * Scroll animations, counters, and interactive effects
 */

(function() {
    'use strict';

    // ============================================
    // Scroll Animation Observer
    // ============================================
    const observerOptions = {
        root: null,
        rootMargin: '0px',
        threshold: 0.1
    };

    const fadeInObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                // For counters, trigger animation
                if (entry.target.hasAttribute('data-count')) {
                    animateCounter(entry.target);
                    fadeInObserver.unobserve(entry.target);
                }
            }
        });
    }, observerOptions);

    // Observe all fade-in elements
    function initScrollAnimations() {
        const fadeElements = document.querySelectorAll('.fade-in');
        fadeElements.forEach(el => fadeInObserver.observe(el));
    }

    // ============================================
    // Animated Counters
    // ============================================
    function animateCounter(element) {
        const target = parseInt(element.getAttribute('data-count'));
        const duration = 2000; // 2 seconds
        const increment = target / (duration / 16); // 60fps
        let current = 0;

        const timer = setInterval(() => {
            current += increment;
            if (current >= target) {
                element.textContent = formatNumber(target) + (element.getAttribute('data-suffix') || '');
                clearInterval(timer);
            } else {
                element.textContent = formatNumber(Math.floor(current)) + (element.getAttribute('data-suffix') || '');
            }
        }, 16);
    }

    function formatNumber(num) {
        if (num >= 1000000) {
            return (num / 1000000).toFixed(1) + 'M';
        } else if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'K';
        }
        return num.toString();
    }

    // ============================================
    // Floating Particles in Hero
    // ============================================
    function createParticles() {
        const heroParticles = document.querySelector('.hero-particles');
        if (!heroParticles) return;

        const particleCount = 50;
        for (let i = 0; i < particleCount; i++) {
            const particle = document.createElement('div');
            particle.className = 'particle';
            particle.style.left = Math.random() * 100 + '%';
            particle.style.animationDelay = Math.random() * 20 + 's';
            particle.style.animationDuration = (15 + Math.random() * 10) + 's';
            heroParticles.appendChild(particle);
        }
    }

    // ============================================
    // Smooth Scroll for Anchor Links
    // ============================================
    function initSmoothScroll() {
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                const href = this.getAttribute('href');
                if (href === '#') return;

                const target = document.querySelector(href);
                if (target) {
                    e.preventDefault();
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    }

    // ============================================
    // Typing Effect for Hero Subtitle (optional)
    // ============================================
    function typeWriter(element, text, speed = 50) {
        if (!element) return;

        let i = 0;
        element.textContent = '';

        function type() {
            if (i < text.length) {
                element.textContent += text.charAt(i);
                i++;
                setTimeout(type, speed);
            }
        }

        type();
    }

    // ============================================
    // Code Block Syntax Highlighting (if needed)
    // ============================================
    function enhanceCodeBlocks() {
        // Check if hljs is available
        if (typeof hljs !== 'undefined') {
            document.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
        }
    }

    // ============================================
    // Performance Benchmark Progress Bars
    // ============================================
    function animateBenchmarkBars() {
        const bars = document.querySelectorAll('.benchmark-bar');
        bars.forEach(bar => {
            const width = bar.getAttribute('data-width');
            setTimeout(() => {
                bar.style.width = width;
            }, 100);
        });
    }

    // ============================================
    // Comparison Table Highlight
    // ============================================
    function highlightDjustColumn() {
        const djustCells = document.querySelectorAll('.comparison-table .djust-col');
        djustCells.forEach(cell => {
            cell.style.background = 'rgba(102, 126, 234, 0.05)';
        });
    }

    // ============================================
    // Initialize djust LiveView Client
    // ============================================
    function initLiveViewDemos() {
        // Connect to WebSocket for live demos if not already connected
        const demoElements = document.querySelectorAll('[data-live-view]');

        demoElements.forEach(element => {
            const viewId = element.getAttribute('data-live-view');
            // The LiveView client will automatically handle this
            console.log('LiveView demo initialized:', viewId);
        });
    }

    // ============================================
    // Scroll Progress Indicator
    // ============================================
    function createScrollProgress() {
        const progressBar = document.createElement('div');
        progressBar.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 0%;
            height: 4px;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            z-index: 9999;
            transition: width 0.1s ease;
        `;
        document.body.appendChild(progressBar);

        window.addEventListener('scroll', () => {
            const windowHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
            const scrolled = (window.scrollY / windowHeight) * 100;
            progressBar.style.width = scrolled + '%';
        });
    }

    // ============================================
    // Parallax Effect for Hero (subtle)
    // ============================================
    function initParallax() {
        const hero = document.querySelector('.hero-section');
        if (!hero) return;

        window.addEventListener('scroll', () => {
            const scrolled = window.scrollY;
            const heroContent = hero.querySelector('.hero-content');
            if (heroContent && scrolled < window.innerHeight) {
                heroContent.style.transform = `translateY(${scrolled * 0.3}px)`;
                heroContent.style.opacity = 1 - (scrolled / window.innerHeight);
            }
        });
    }

    // ============================================
    // Easter Egg: Konami Code
    // ============================================
    function initKonamiCode() {
        const konamiCode = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'b', 'a'];
        let konamiIndex = 0;

        document.addEventListener('keydown', (e) => {
            if (e.key === konamiCode[konamiIndex]) {
                konamiIndex++;
                if (konamiIndex === konamiCode.length) {
                    triggerEasterEgg();
                    konamiIndex = 0;
                }
            } else {
                konamiIndex = 0;
            }
        });
    }

    function triggerEasterEgg() {
        // Fun easter egg: make the page super fast
        document.body.style.animation = 'spin 2s ease-in-out';

        const style = document.createElement('style');
        style.textContent = `
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
        `;
        document.head.appendChild(style);

        setTimeout(() => {
            document.body.style.animation = '';
            alert('🚀 You found the secret! djust is even faster now! (Just kidding, it was already blazing fast)');
        }, 2000);
    }

    // ============================================
    // Copy to Clipboard for Code Examples
    // ============================================
    function initCopyButtons() {
        document.querySelectorAll('.code-block').forEach(codeBlock => {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-btn';
            copyBtn.innerHTML = '📋 Copy';
            copyBtn.style.cssText = `
                position: absolute;
                top: 10px;
                right: 10px;
                padding: 0.5rem 1rem;
                background: rgba(255, 255, 255, 0.9);
                border: none;
                border-radius: 6px;
                cursor: pointer;
                font-size: 0.875rem;
                transition: all 0.2s ease;
            `;

            const codeHeader = codeBlock.querySelector('.code-header');
            if (codeHeader) {
                codeHeader.style.position = 'relative';
                codeHeader.appendChild(copyBtn);

                copyBtn.addEventListener('click', () => {
                    const code = codeBlock.querySelector('code');
                    if (code) {
                        navigator.clipboard.writeText(code.textContent).then(() => {
                            copyBtn.innerHTML = '✅ Copied!';
                            setTimeout(() => {
                                copyBtn.innerHTML = '📋 Copy';
                            }, 2000);
                        });
                    }
                });

                copyBtn.addEventListener('mouseenter', () => {
                    copyBtn.style.background = 'white';
                    copyBtn.style.transform = 'scale(1.05)';
                });

                copyBtn.addEventListener('mouseleave', () => {
                    copyBtn.style.background = 'rgba(255, 255, 255, 0.9)';
                    copyBtn.style.transform = 'scale(1)';
                });
            }
        });
    }

    // ============================================
    // Lazy Load Images (if needed)
    // ============================================
    function initLazyLoad() {
        const images = document.querySelectorAll('img[data-src]');

        const imageObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    img.src = img.getAttribute('data-src');
                    img.removeAttribute('data-src');
                    imageObserver.unobserve(img);
                }
            });
        });

        images.forEach(img => imageObserver.observe(img));
    }

    // ============================================
    // Main Initialization
    // ============================================
    function init() {
        console.log('🚀 djust homepage initialized');

        // Core functionality
        initScrollAnimations();
        initSmoothScroll();
        createParticles();
        createScrollProgress();

        // Enhancements
        enhanceCodeBlocks();
        initCopyButtons();
        initLazyLoad();
        initParallax();

        // LiveView
        initLiveViewDemos();

        // Easter eggs
        initKonamiCode();

        // Additional animations when available
        if (document.querySelector('.benchmark-bar')) {
            animateBenchmarkBars();
        }

        if (document.querySelector('.comparison-table')) {
            highlightDjustColumn();
        }
    }

    // Run when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Export for potential external use
    window.djustHomepage = {
        animateCounter,
        typeWriter,
        initScrollAnimations
    };
})();
