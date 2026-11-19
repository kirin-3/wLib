// SPDX-License-Identifier: GPL-3.0-or-later
(() => {
    if (globalThis.wlibLewdCornerLoaded) return;
    globalThis.wlibLewdCornerLoaded = true;

    const threadId = /^\/threads\/(?:[^/]+\.)?([1-9][0-9]*)(?:\/.*)?$/i.exec(location.pathname)?.[1];
    const threadUrl = id => `https://lewdcorner.com/threads/${id}/`;
    if (threadId) {
        const heading = document.querySelector('h1.p-title-value');
        const cleanHeading = heading?.cloneNode(true);
        cleanHeading?.querySelectorAll('.label').forEach(label => label.remove());
        const post = document.querySelector('article.message--post, article.message');
        const text = node => node?.textContent?.trim() || '';
        // A screenshot is a small /data/attachments/ copy inside a link to the
        // full /attachments/<name>.<id>/ image; the link is the URL to keep.
        const imageUrl = img => {
            const link = img.closest('a[href]')?.href || '';
            if (/^https:\/\/lewdcorner\.com\/attachments\/[^/?#]+\.\d+\/$/.test(link)) return link;
            return img.currentSrc || img.src || '';
        };
        // A title label links to "?prefix_id=<id>". The id tells apart the two
        // LC prefixes named "Other" (engine 22, render 29). Names stay plain
        // strings so an older wLib still reads them; ids go in a parallel list.
        const prefixLabels = Array.from(heading?.querySelectorAll('.label') || []).filter(label => text(label));
        const prefixId = label => {
            const id = /[?&]prefix_id=([1-9][0-9]*)/.exec(label.closest?.('a.labelLink')?.href || '')?.[1];
            return id ? Number(id) : null;
        };
        const images = [...new Set(Array.from(post?.querySelectorAll('.message-userContent img') || [], imageUrl)
            .filter(url => /^https:\/\//i.test(url)))];
        WLibSiteWidget.show(null, [], {
            url: threadUrl(threadId),
            title: text(cleanHeading) || document.title,
            prefixes: prefixLabels.map(text),
            prefix_ids: prefixLabels.map(prefixId),
            developer: text(post?.querySelector('[data-field="Developer" i] dd')),
            version: text(post?.querySelector('[data-field="version" i] dd')),
            tags: Array.from(document.querySelectorAll('.tagList .tagItem'), text).filter(Boolean),
            coverImage: images[0] || '',
            screenshots: images.slice(1),
        });
        return;
    }
    if (!/^\/latest-updates\/?$/i.test(location.pathname)) return;

    let scheduled = false;
    const selector = 'article.lcCard[data-card-id]';
    const badgeSelector = '.wlib-library-badge';
    function scan() {
        scheduled = false;
        const tiles = new Map();
        for (const tile of document.querySelectorAll(selector)) {
            const id = tile.dataset.cardId;
            if (!/^[1-9][0-9]*$/.test(id || '') || tile.dataset.wlibChecked === id) continue;
            if (!tiles.has(id)) tiles.set(id, []);
            tiles.get(id).push(tile);
            tile.dataset.wlibChecked = id;
            tile.querySelector(badgeSelector)?.remove();
        }
        if (!tiles.size) return;
        try {
            chrome.runtime.sendMessage({action:'checkLewdCornerThreads',threadIds:[...tiles.keys()]}, response => {
                if (chrome.runtime.lastError || !response?.success) return;
                for (const [id, matchingTiles] of tiles) {
                    if (!Object.hasOwn(response.matches, threadUrl(id))) continue;
                    for (const tile of matchingTiles) {
                        if (!tile.isConnected || tile.dataset.cardId !== id || tile.querySelector(badgeSelector)) continue;
                        const badge = document.createElement('span');
                        badge.className = 'wlib-library-badge';
                        badge.textContent = 'In wLib';
                        // LC's image strip (.lcCardMedia, z-index 4) covers anything placed
                        // on the card itself. It is already positioned, and its menu must
                        // not be clipped, so it gets the badge without the host class.
                        const media = tile.querySelector('.lcCardMedia');
                        if (media) media.append(badge);
                        else {
                            tile.classList.add('wlib-badge-host');
                            tile.append(badge);
                        }
                    }
                }
            });
        } catch {
            // The extension may have been reloaded while the page was open.
        }
    }

    const observer = new MutationObserver(() => {
        if (!scheduled) {
            scheduled = true;
            setTimeout(scan, 40);
        }
    });
    observer.observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['data-card-id']});
    scan();
})();
