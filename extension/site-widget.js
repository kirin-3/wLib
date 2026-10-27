// SPDX-License-Identifier: GPL-3.0-or-later
(() => {
    const send = message => new Promise(resolve => {
        try { chrome.runtime.sendMessage(message,response => { const error = chrome.runtime.lastError; resolve(error ? {success:false,error:error.message} : response || {success:false}); }); }
        catch (error) { resolve({success:false,error:error.message}); }
    });
    function show(page, extra = [], metadata = {}) {
        if (!page || document.getElementById('wlib-store-widget')) return;
        const links = [...new Map([page,...extra].filter(Boolean).map(link => [`${link.source}:${link.id}`,link])).values()];
        const payload = {url:'',pageUrl:location.href,links,title:document.querySelector('meta[property="og:title"]')?.content || document.querySelector('h1')?.textContent?.trim() || document.title,coverImage:document.querySelector('meta[property="og:image"]')?.content || '',tags:[]};
        Object.assign(payload, metadata);
        const ids = links.map(link => `${link.source}:${link.id}`);
        const root = document.createElement('section'); root.id = 'wlib-store-widget'; root.className = 'wlib-add-container'; root.setAttribute('aria-label','wLib');
        const heading = document.createElement('strong'); heading.className = 'wlib-header-row'; heading.textContent = 'wLib'; root.append(heading);
        const actions = document.createElement('div'); actions.className = 'wlib-action-row'; root.append(actions);
        const status = document.createElement('p'); status.className = 'wlib-status'; status.setAttribute('aria-live','polite'); root.append(status);
        const warning = document.createElement('p'); warning.className = 'wlib-outdated-warning'; warning.hidden = true; warning.textContent = 'Your wLib app is out of date. Some actions may not work.'; root.append(warning);
        document.body.append(root);
        function button(label, handler) {
            const button = document.createElement('button'); button.type = 'button'; button.className = 'wlib-add-btn'; button.textContent = label;
            button.addEventListener('click', async () => {
                const buttons = [...actions.querySelectorAll('button')]; buttons.forEach(item => {item.disabled = true;});
                try { await handler(); } finally { buttons.forEach(item => {item.disabled = false;}); }
            }); actions.append(button);
        }
        function failure() { status.textContent = 'Make sure wLib is open, then retry.'; actions.replaceChildren(); button('Retry',check); }
        async function check() {
            status.textContent = 'Checking wLib…';
            const response = await send({action:'checkGameInWLib',url:location.href,ids});
            if (!response.success || !response.data) { failure(); return; }
            const state = response.data;
            warning.hidden = !state.appOutdated;
            actions.replaceChildren();
            status.textContent = state.exists ? (state.wishlisted ? 'Wishlisted' : 'In library') : '';
            if (state.exists) button('Open in wLib',async () => {
                const reply = await send({action:'openWLib',url:location.href,ids});
                if (!reply.success) failure(); else status.textContent = 'Opened in wLib';
            });
            else {
                button('Add to wLib',async () => {
                    const reply = await send({action:'addGameToWLib',payload});
                    if (!reply.success) failure(); else status.textContent = 'Sent to wLib';
                });
                button('Wishlist',async () => {
                    const reply = await send({action:'wishlistGameInWLib',payload});
                    if (reply.success || reply.error_code === 'duplicate_url') {
                        await check(); status.textContent = reply.success ? 'Wishlisted' : 'Already in wLib';
                    } else failure();
                });
            }
        }
        void check();
    }
    globalThis.WLibSiteWidget = {show};
})();
