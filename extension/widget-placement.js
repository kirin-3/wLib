// SPDX-License-Identifier: GPL-3.0-or-later
(() => {
    const key = 'widgetPlacement';
    const normalize = value => ({
        side: value?.side === 'left' ? 'left' : 'right',
        collapsed: typeof value?.collapsed === 'boolean' ? value.collapsed : false,
    });
    function load() {
        return new Promise(resolve => {
            try {
                if (!globalThis.chrome?.storage?.local) return resolve(normalize());
                chrome.storage.local.get(key, result => {
                    resolve(normalize(chrome.runtime?.lastError ? undefined : result?.[key]));
                });
            } catch (_error) { resolve(normalize()); }
        });
    }
    function save(state) {
        try {
            chrome.storage.local.set({[key]: normalize(state)}, () => {
                if (chrome.runtime?.lastError) console.warn('wLib placement:', chrome.runtime.lastError.message);
            });
        } catch (error) { console.warn('wLib placement:', error); }
    }
    function watch(callback) {
        try {
            chrome.storage.onChanged.addListener((changes, area) => {
                if (area === 'local' && Object.hasOwn(changes, key)) callback(normalize(changes[key].newValue));
            });
        } catch (_error) { /* Storage may be unavailable. */ }
    }
    function apply(container, value, {animate = true} = {}) {
        const state = normalize(value);
        if (!animate) container.classList.add('is-placement-instant');
        container.setAttribute('data-side', state.side);
        container.classList.toggle('is-collapsed', state.collapsed);
        container.setAttribute('data-state', state.collapsed ? 'collapsed' : 'expanded');
        const side = container.querySelector('.wlib-side-btn');
        const collapse = container.querySelector('.wlib-collapse-btn');
        const expand = container.querySelector('.wlib-collapsed-toggle');
        if (side) { side.setAttribute('aria-label', `Move wLib widget to the ${state.side === 'left' ? 'right' : 'left'}`); side.tabIndex = state.collapsed ? -1 : 0; }
        if (collapse) collapse.tabIndex = state.collapsed ? -1 : 0;
        if (expand) expand.tabIndex = state.collapsed ? 0 : -1;
        if (!animate) {
            void container.offsetWidth;
            // Call requestAnimationFrame directly: an unbound copy throws in Firefox content scripts.
            const restore = () => container.classList.remove('is-placement-instant');
            if (typeof requestAnimationFrame === 'function') requestAnimationFrame(restore);
            else setTimeout(restore, 0);
        }
    }
    globalThis.WLibWidgetPlacement = {load, save, watch, apply};
})();
