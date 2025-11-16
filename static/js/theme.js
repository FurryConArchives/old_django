/**
 * Persist light/dark theme and keep the browser chrome in sync.
 */
(function () {
    const STORAGE_KEY = 'fca-theme';
    const root = document.documentElement;

    function currentTheme() {
        return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    }

    function applyTheme(theme) {
        const next = theme === 'light' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        root.style.colorScheme = next;
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute('content', next === 'light' ? '#007fa8' : '#00beee');
        }
        const status = document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]');
        if (status) {
            status.setAttribute('content', next === 'light' ? 'default' : 'black-translucent');
        }
        const toggle = document.getElementById('theme-toggle');
        if (toggle) {
            const label = next === 'light' ? 'Switch to dark mode' : 'Switch to light mode';
            toggle.setAttribute('aria-label', label);
            toggle.setAttribute('title', label);
            toggle.setAttribute('aria-pressed', next === 'light' ? 'true' : 'false');
        }
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch (e) {
            /* private mode */
        }
    }

    function init() {
        const toggle = document.getElementById('theme-toggle');
        applyTheme(currentTheme());
        if (!toggle) return;
        toggle.addEventListener('click', function () {
            applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
