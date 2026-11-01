// SPDX-License-Identifier: GPL-3.0-or-later
// Store-page widget for DLsite, VNDB and Ryuugames. Builds the same markup as
// the F95 widget in content.js so both share content.css.
(() => {
    const send = message => new Promise(resolve => {
        try { chrome.runtime.sendMessage(message,response => { const error = chrome.runtime.lastError; resolve(error ? {success:false,error:error.message} : response || {success:false}); }); }
        catch (error) { resolve({success:false,error:error.message}); }
    });
    const placementHelper = globalThis.WLibWidgetPlacement;
    const placementPromise = placementHelper ? placementHelper.load() : Promise.resolve({side:'right',collapsed:false});
    let showing = false;
    const icons = {arrowRight:'arrow-right.svg',arrowDownLeft:'arrow-down-left.svg',libraryPlus:'library-plus.svg',externalLink:'external-link.svg'};
    function image(file, className, size, alt = '') {
        const img = document.createElement('img'); img.className = className; img.src = chrome.runtime.getURL(file); img.alt = alt;
        img.width = size; img.height = size; img.decoding = 'async'; if (!alt) img.setAttribute('aria-hidden','true');
        return img;
    }
    const logo = () => image('icon.svg','wlib-logo',28,'wLib');
    const icon = name => image(icons[name],'wlib-icon',16);
    const toneClasses = {'plan to play':'plan-to-play',playing:'playing','waiting for update':'waiting','on hold':'on-hold',completed:'completed',abandoned:'abandoned'};
    const playStatusClass = status => `wlib-play-status--${toneClasses[String(status || '').trim().toLowerCase()] || 'not-started'}`;
    async function show(page, extra = [], metadata = {}) {
        if (!page || showing || document.getElementById('wlib-store-widget')) return;
        showing = true;
        const links = [...new Map([page,...extra].filter(Boolean).map(link => [`${link.source}:${link.id}`,link])).values()];
        const payload = {url:'',pageUrl:location.href,links,title:document.querySelector('meta[property="og:title"]')?.content || document.querySelector('h1')?.textContent?.trim() || document.title,coverImage:document.querySelector('meta[property="og:image"]')?.content || '',tags:[]};
        Object.assign(payload, metadata);
        const ids = links.map(link => `${link.source}:${link.id}`);
        const root = document.createElement('section'); root.id = 'wlib-store-widget'; root.className = 'wlib-add-container wlib-site-widget'; root.setAttribute('aria-label','wLib');
        const header = document.createElement('div'); header.className = 'wlib-header-row';
        const brand = document.createElement('div'); brand.className = 'wlib-brand'; brand.append(logo());
        const collapse = document.createElement('button'); collapse.type = 'button'; collapse.className = 'wlib-collapse-btn'; collapse.setAttribute('aria-label','Collapse wLib widget'); collapse.append(icon('arrowDownLeft'));
        const side = document.createElement('button'); side.type = 'button'; side.className = 'wlib-side-btn'; side.append(icon('arrowRight'));
        const headerActions = document.createElement('div'); headerActions.className = 'wlib-header-actions'; headerActions.append(side,collapse);
        header.append(brand,headerActions);
        const body = document.createElement('div'); body.className = 'wlib-widget-body';
        const warning = document.createElement('p'); warning.className = 'wlib-outdated-warning'; warning.hidden = true; warning.textContent = 'Your wLib app is out of date. Some actions may not work.';
        const actions = document.createElement('div'); actions.className = 'wlib-action-row';
        const library = document.createElement('div'); library.className = 'wlib-library-status-block';
        const status = document.createElement('p'); status.className = 'wlib-status'; status.setAttribute('aria-live','polite');
        body.append(warning,actions,library,status);
        const expand = document.createElement('button'); expand.type = 'button'; expand.className = 'wlib-collapsed-toggle'; expand.setAttribute('aria-label','Expand wLib widget'); expand.append(logo());
        root.append(header,body,expand);
        let placement = await placementPromise;
        const apply = (state, animate = true) => {
            if (placementHelper) placementHelper.apply(root,state,{animate});
            else {
                root.dataset.side = state.side; root.dataset.state = state.collapsed ? 'collapsed' : 'expanded';
                root.classList.toggle('is-collapsed',state.collapsed);
                side.setAttribute('aria-label',`Move wLib widget to the ${state.side === 'left' ? 'right' : 'left'}`);
                side.tabIndex = collapse.tabIndex = state.collapsed ? -1 : 0; expand.tabIndex = state.collapsed ? 0 : -1;
            }
        };
        const update = next => { placement = next; apply(next); placementHelper?.save(next); };
        side.addEventListener('click',() => update({...placement,side:placement.side === 'left' ? 'right' : 'left'}));
        collapse.addEventListener('click',() => update({...placement,collapsed:true}));
        expand.addEventListener('click',() => update({...placement,collapsed:false}));
        placementHelper?.watch(next => {
            if (next.side === placement.side && next.collapsed === placement.collapsed) return;
            placement = next; apply(next);
        });
        apply(placement,false);
        document.body.append(root);
        function button(label, handler, {iconName = '', variant = ''} = {}) {
            const button = document.createElement('button'); button.type = 'button';
            button.className = `wlib-add-btn${variant === 'secondary' ? ' wlib-wishlist-btn' : ''}${variant === 'open' ? ' is-open-action' : ''}`;
            const text = document.createElement('span'); text.textContent = label;
            button.replaceChildren(...(iconName ? [icon(iconName)] : []),text);
            button.addEventListener('click', async () => {
                const buttons = [...actions.querySelectorAll('button')]; buttons.forEach(item => {item.disabled = true;});
                try { await handler(); } finally { buttons.forEach(item => {item.disabled = false;}); }
            }); actions.append(button);
        }
        function showLibraryStatus(state) {
            library.replaceChildren();
            if (!state.exists) return;
            const playStatus = state.wishlisted ? 'Wishlisted' : (typeof state.playStatus === 'string' && state.playStatus.trim()) || 'Not Started';
            const divider = document.createElement('div'); divider.className = 'wlib-status-divider';
            const line = document.createElement('p'); line.className = 'wlib-library-status-line'; line.append('Status: ');
            const value = document.createElement('span'); value.className = `wlib-play-status ${playStatusClass(playStatus)}`; value.textContent = playStatus;
            line.append(value); library.append(divider,line);
        }
        function failure() { status.textContent = 'Make sure wLib is open, then retry.'; library.replaceChildren(); actions.replaceChildren(); button('Retry',check,{iconName:'libraryPlus'}); }
        async function check() {
            status.textContent = 'Checking wLib…';
            const response = await send({action:'checkGameInWLib',url:location.href,ids});
            if (!response.success || !response.data) { failure(); return; }
            const state = response.data;
            warning.hidden = !state.appOutdated;
            actions.replaceChildren();
            status.textContent = '';
            showLibraryStatus(state);
            if (state.exists) button('Open in wLib',async () => {
                const reply = await send({action:'openWLib',url:location.href,ids});
                if (!reply.success) failure(); else status.textContent = 'Brought wLib to front.';
            },{iconName:'externalLink',variant:'open'});
            else {
                button('Add to wLib',async () => {
                    const reply = await send({action:'addGameToWLib',payload});
                    if (!reply.success) failure(); else status.textContent = 'Game sent to wLib successfully.';
                },{iconName:'libraryPlus'});
                button('Wishlist',async () => {
                    const reply = await send({action:'wishlistGameInWLib',payload});
                    if (reply.success || reply.error_code === 'duplicate_url') {
                        await check(); status.textContent = reply.success ? 'Wishlisted' : 'Already in wLib';
                    } else failure();
                },{variant:'secondary'});
            }
        }
        void check();
    }
    globalThis.WLibSiteWidget = {show};
})();
