// SPDX-License-Identifier: GPL-3.0-or-later
const WLIB_UPDATES_PAGE_PATH = '/sam/latest_alpha/';
const WLIB_THREAD_ROOT_ID = 'wlib-extension-root';
const WLIB_TILE_BADGE_SELECTOR = '.wlib-library-badge';
const WLIB_DEFAULT_PLAY_STATUS = 'Not Started';

function getLogoUrl() {
    return getAssetUrl('icon.svg');
}

function getAssetUrl(filename) {
    if (typeof chrome !== 'undefined' && chrome.runtime && typeof chrome.runtime.getURL === 'function') {
        return chrome.runtime.getURL(filename);
    }

    return filename;
}

function createLogoImage() {
    const image = document.createElement('img');
    image.className = 'wlib-logo';
    image.src = getLogoUrl();
    image.alt = 'wLib';
    image.width = 28;
    image.height = 28;
    image.decoding = 'async';
    return image;
}

const WLIB_ICON_FILES = {
    arrowDownLeft: 'arrow-down-left.svg',
    libraryPlus: 'library-plus.svg',
    externalLink: 'external-link.svg',
};

function createIconImage(iconName) {
    const filename = WLIB_ICON_FILES[iconName];
    const image = document.createElement('img');
    image.className = 'wlib-icon';
    image.src = getAssetUrl(filename);
    image.alt = '';
    image.width = 16;
    image.height = 16;
    image.decoding = 'async';
    image.setAttribute('aria-hidden', 'true');
    return image;
}

const WLIB_PLAY_STATUS_TONE_CLASS_MAP = {
    'not started': 'wlib-play-status--not-started',
    'plan to play': 'wlib-play-status--plan-to-play',
    playing: 'wlib-play-status--playing',
    'waiting for update': 'wlib-play-status--waiting',
    'on hold': 'wlib-play-status--on-hold',
    completed: 'wlib-play-status--completed',
    abandoned: 'wlib-play-status--abandoned',
};

const wlibTileCheckCache = new Map();
const wlibPendingTileChecks = new Set();

let wlibLatestAlphaObserver = null;
let wlibLatestAlphaScanTimer = 0;
let wlibLatestAlphaHashListenerAttached = false;

function getThreadIdentity(rawUrl = window.location.href) {
    try {
        const parsed = new URL(rawUrl, window.location.origin);
        // Thread IDs are only meaningful on F95Zone (same host allowlist as
        // F95_HOSTS in core/f95zone.py). Another site's /threads/<id>/ must
        // not be checked against the library as an F95 thread.
        const host = (parsed.hostname || '').toLowerCase();
        if (host !== 'f95zone.to' && host !== 'www.f95zone.to') {
            return {
                id: '',
                canonicalUrl: parsed.toString(),
            };
        }
        const match = parsed.pathname.match(/^\/threads\/(?:(.+)\.)?(\d+)(?:\/.*)?$/);
        if (match) {
            const slug = (match[1] || '').replace(/^\.+|\.+$/g, '');
            const threadPath = slug
                ? `/threads/${slug}.${match[2]}/`
                : `/threads/${match[2]}/`;

            parsed.pathname = threadPath;
            parsed.search = '';
            parsed.hash = '';

            return {
                id: match[2],
                canonicalUrl: parsed.toString(),
            };
        }

        parsed.search = '';
        parsed.hash = '';
        return {
            id: '',
            canonicalUrl: parsed.toString(),
        };
    } catch (_error) {
        return {
            id: '',
            canonicalUrl: rawUrl,
        };
    }
}

function isThreadPage() {
    return window.location.pathname.startsWith('/threads/');
}

function isLatestAlphaPage() {
    // Tolerate a missing trailing slash: the manifest match
    // (sam/latest_alpha*) fires for both, and F95 links omit it at times.
    // window.location.pathname never contains the query string.
    const canonical = WLIB_UPDATES_PAGE_PATH.replace(/\/+$/, '');
    return window.location.pathname.replace(/\/+$/, '') === canonical;
}

function normalizeCheckResult(data) {
    const exists = Boolean(data && typeof data === 'object' && data.exists);
    const playStatus = exists && data && typeof data.playStatus === 'string'
        ? data.playStatus.trim()
        : '';

    return {
        exists,
        canAttachThread: exists && Boolean(data?.canAttachThread),
        playStatus,
        wishlisted: exists && Boolean(data.wishlisted),
        appOutdated: Boolean(data && typeof data === 'object' && data.appOutdated),
    };
}

function isExtensionContextInvalidated() {
    try {
        return typeof chrome !== 'undefined' && !!chrome.runtime && chrome.runtime.id === undefined;
    } catch (_error) {
        return false;
    }
}

function wlibConnectionStatusMessage() {
    return isExtensionContextInvalidated()
        ? 'wLib extension updated. Refresh this page.'
        : 'Error: Make sure wLib is open.';
}

function checkGameInWLib(url, links = []) {
    return new Promise((resolve) => {
        chrome.runtime.sendMessage({
            action: 'checkGameInWLib',
            url,
            ids: links.map(link => `${link.source}:${link.id}`),
        }, (response) => {
            if (chrome.runtime.lastError) {
                console.warn('wLib extension: Could not connect to background wLib service.', chrome.runtime.lastError);
                resolve({ exists: false, playStatus: '', wishlisted: false, appOutdated: false });
                return;
            }

            if (response && response.success && response.data) {
                resolve(normalizeCheckResult(response.data));
                return;
            }

            console.warn('wLib extension: Check request failed.', response?.error || response);
            resolve({ exists: false, playStatus: '', wishlisted: false, appOutdated: false });
        });
    });
}

function initWLibExtension() {
    if (isThreadPage()) {
        initThreadPageExtension();
    }

    if (isLatestAlphaPage()) {
        initLatestAlphaPageExtension();
    }
}

async function initThreadPageExtension() {
    if (document.getElementById(WLIB_THREAD_ROOT_ID)) return;

    const gameInfo = extractGameInfo();
    if (!gameInfo.title) return;

    const wrapper = document.querySelector('.message-threadStarterPost .bbWrapper');
    const links = globalThis.WLibStoreLinks?.collectF95Links(wrapper);
    gameInfo.links = links || [];
    const checkResult = await checkGameInWLib(gameInfo.url, gameInfo.links);
    if (checkResult.exists && links !== null && links !== undefined) {
        chrome.runtime.sendMessage({action:"saveThreadLinks", payload:{url:gameInfo.url, links}}, () => { void chrome.runtime.lastError; });
    }
    gameInfo.existsInWLib = checkResult.exists;
    gameInfo.playStatus = checkResult.playStatus;
    gameInfo.wishlisted = checkResult.wishlisted;
    gameInfo.appOutdated = checkResult.appOutdated;
    gameInfo.canAttachThread = checkResult.canAttachThread;
    injectUI(gameInfo);
}

function initLatestAlphaPageExtension() {
    if (!wlibLatestAlphaHashListenerAttached) {
        window.addEventListener('hashchange', scheduleLatestAlphaTileScan);
        wlibLatestAlphaHashListenerAttached = true;
    }

    if (!wlibLatestAlphaObserver) {
        wlibLatestAlphaObserver = new MutationObserver((mutations) => {
            if (!isLatestAlphaPage()) {
                return;
            }

            const hasRelevantMutation = mutations.some((mutation) => {
                if (mutation.type === 'childList') {
                    return mutation.addedNodes.length > 0 || mutation.removedNodes.length > 0;
                }

                return mutation.type === 'attributes';
            });

            if (hasRelevantMutation) {
                scheduleLatestAlphaTileScan();
            }
        });

        wlibLatestAlphaObserver.observe(document.body, {
            childList: true,
            subtree: true,
        });
    }

    scheduleLatestAlphaTileScan();
}

function scheduleLatestAlphaTileScan() {
    if (!isLatestAlphaPage()) {
        return;
    }

    if (wlibLatestAlphaScanTimer) {
        window.clearTimeout(wlibLatestAlphaScanTimer);
    }

    wlibLatestAlphaScanTimer = window.setTimeout(() => {
        wlibLatestAlphaScanTimer = 0;
        scanLatestAlphaTiles();
    }, 60);
}

function scanLatestAlphaTiles() {
    if (!isLatestAlphaPage()) {
        return;
    }

    const tileLinks = document.querySelectorAll('a.resource-tile_link[href*="/threads/"]');
    tileLinks.forEach((tileLink) => {
        processLatestAlphaTile(tileLink);
    });
}

function processLatestAlphaTile(tileLink) {
    if (!(tileLink instanceof HTMLAnchorElement)) {
        return;
    }

    const threadIdentity = getThreadIdentity(tileLink.href);
    if (!threadIdentity.id || !threadIdentity.canonicalUrl) {
        return;
    }

    const thumb = tileLink.querySelector('.resource-tile_thumb');
    if (!(thumb instanceof HTMLElement)) {
        return;
    }

    thumb.classList.add('wlib-badge-host');
    thumb.dataset.wlibThreadUrl = threadIdentity.canonicalUrl;

    const cachedResult = wlibTileCheckCache.get(threadIdentity.canonicalUrl);
    if (typeof cachedResult === 'boolean') {
        if (cachedResult) {
            ensureLibraryBadge(thumb);
        } else {
            removeLibraryBadge(thumb);
        }
        return;
    }

    if (wlibPendingTileChecks.has(threadIdentity.canonicalUrl)) {
        return;
    }

    wlibPendingTileChecks.add(threadIdentity.canonicalUrl);
    checkGameInWLib(threadIdentity.canonicalUrl)
        .then((checkResult) => {
            const existsInWLib = checkResult.exists;
            wlibTileCheckCache.set(threadIdentity.canonicalUrl, existsInWLib);
            if (!document.contains(thumb)) {
                return;
            }

            if (thumb.dataset.wlibThreadUrl !== threadIdentity.canonicalUrl) {
                return;
            }

            if (existsInWLib) {
                ensureLibraryBadge(thumb);
            } else {
                removeLibraryBadge(thumb);
            }
        })
        .finally(() => {
            wlibPendingTileChecks.delete(threadIdentity.canonicalUrl);
        });
}

function ensureLibraryBadge(thumb) {
    if (thumb.querySelector(WLIB_TILE_BADGE_SELECTOR)) {
        return;
    }

    const badge = document.createElement('div');
    badge.className = 'wlib-library-badge';
    badge.textContent = 'In wLib';
    thumb.appendChild(badge);
}

function removeLibraryBadge(thumb) {
    const badge = thumb.querySelector(WLIB_TILE_BADGE_SELECTOR);
    if (badge) {
        badge.remove();
    }
}

function extractGameInfo() {
    const threadIdentity = getThreadIdentity();
    const info = {
        title: '',
        developer: '',
        version: '',
        engine: '',
        tags: [],
        rating: '',
        coverImage: '',
        url: threadIdentity.canonicalUrl,
        id: threadIdentity.id,
        playStatus: '',
    };

    try {
        const h1 = document.querySelector('h1.p-title-value');
        if (h1) {
            // Same list as NON_ENGINE_PREFIXES in core/f95zone.py.
            // Compared after lowercasing and stripping spaces/hyphens/underscores.
            const nonEngineLabels = new Set(['completed', 'abandoned', 'onhold', 'vn', 'collection', 'siterip', 'readme']);
            const normalizePrefix = (label) => String(label || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const engineLabel = [...h1.querySelectorAll('a.labelLink')]
                .map(label => label.textContent.trim())
                .find(label => label && !nonEngineLabels.has(normalizePrefix(label)));
            info.engine = engineLabel || '';

            const h1Clone = h1.cloneNode(true);
            h1Clone.querySelectorAll('a.labelLink, span.label-append').forEach(el => el.remove());

            // Mirrored in core/f95zone.py split_thread_title (h1 title parsing).
            let fullTitleText = h1Clone.textContent.trim();
            fullTitleText = fullTitleText.replace(/^xLibrary\s*-\s*/i, '');
            fullTitleText = fullTitleText.replace(/^wLib\s*-\s*/i, '');
            // Strip leading non-version brackets (e.g. "[RenPy]"), but keep a
            // leading version like "[v1.0]" so it is not lost.
            for (;;) {
                const leadingBracket = fullTitleText.match(/^\[([^\]]+)\]\s*/);
                if (!leadingBracket) break;
                const innerBracket = leadingBracket[1].trim();
                if (/^v?(?:er(?:sion)?\.?\s*)?(\d+[\d.]*[a-zA-Z]?(?:\s*(?:beta|alpha|final|fix\d*|hotfix))?)$/i.test(innerBracket)) break;
                if (/^(Ch(?:apter|\.)?\s*\d+(?:\.\d+)?(?:\s*v[\d.]+[a-zA-Z]?)?|Ep(?:isode|\.)?\s*\d+(?:\.\d+)?|S\d+\s*E\d+|Part\s*\d+)/i.test(innerBracket)) break;
                fullTitleText = fullTitleText.slice(leadingBracket[0].length);
            }

            const bracketedVersions = [...fullTitleText.matchAll(/\[([^\]]*)\]/g)];
            for (const m of bracketedVersions) {
                const inner = m[1].trim();
                if (/^v?(?:er(?:sion)?\.?\s*)?(\d+[\d.]*[a-zA-Z]?(?:\s*(?:beta|alpha|final|fix\d*|hotfix))?)$/i.test(inner)) {
                    info.version = inner.replace(/^v(?:er(?:sion)?\.?\s*)?/i, '').trim();
                    fullTitleText = fullTitleText.replace(m[0], '');
                    break;
                }

                const chapterMatch = inner.match(/^(Ch(?:apter|\.)?\s*\d+(?:\.\d+)?(?:\s*v[\d.]+[a-zA-Z]?)?|Ep(?:isode|\.)?\s*\d+(?:\.\d+)?|S\d+\s*E\d+|Part\s*\d+)/i);
                if (chapterMatch) {
                    info.version = inner.trim();
                    fullTitleText = fullTitleText.replace(m[0], '');
                    break;
                }
            }

            if (!info.version) {
                const bareVersionMatch = fullTitleText.match(/\bv(\d+[\d.]*[a-zA-Z]?)(?:\s|$|\[)/i);
                if (bareVersionMatch) {
                    info.version = bareVersionMatch[1].trim();
                    fullTitleText = fullTitleText.replace(bareVersionMatch[0], bareVersionMatch[0].endsWith('[') ? '[' : ' ');
                }
            }

            if (!info.version && bracketedVersions.length >= 2) {
                info.version = bracketedVersions[0][1].trim();
                fullTitleText = fullTitleText.replace(bracketedVersions[0][0], '');
            }

            const devMatch = fullTitleText.match(/\[([^\]]+)\]$/);
            if (devMatch) {
                info.developer = devMatch[1].trim();
                fullTitleText = fullTitleText.replace(devMatch[0], '');
            }

            fullTitleText = fullTitleText.replace(/\[.*?\]/g, '').trim();
            info.title = fullTitleText;
        }

        if (!info.version) {
            const firstPost = document.querySelector('.message-body .bbWrapper');
            if (firstPost) {
                const htmlPatterns = [
                    /<b>\s*(?:Current\s+)?Version\s*<\/b>\s*:?\s*v?([^<\n]+)/i,
                    /<b>\s*Ver(?:\.|sion)?\s*<\/b>\s*:?\s*v?([^<\n]+)/i,
                ];
                // Same as _POST_VERSION_RE in core/scraper.py.
                const postVersion = String.raw`\d[^\s,;<]*(?:\s+(?:beta|alpha|final|fix\d*|hotfix)\b)?`;
                let found = false;
                for (const pattern of htmlPatterns) {
                    const htmlMatch = firstPost.innerHTML.match(pattern);
                    const versionMatch = htmlMatch && htmlMatch[1].trim().match(new RegExp(`^${postVersion}`, 'i'));
                    if (versionMatch) {
                        info.version = versionMatch[0];
                        found = true;
                        break;
                    }
                }

                if (!found) {
                    const postText = firstPost.textContent;
                    const textPatterns = [
                        new RegExp(String.raw`(?:Current\s+)?Version\s*:?\s*v?(${postVersion})`, 'i'),
                        new RegExp(String.raw`Ver(?:\.|sion)?\s*:?\s*v?(${postVersion})`, 'i'),
                        /Release\s*:?\s*v?(\d[\d.]*[a-zA-Z]?)/i,
                    ];
                    for (const pattern of textPatterns) {
                        const bodyMatch = postText.match(pattern);
                        if (bodyMatch) {
                            info.version = bodyMatch[1].trim();
                            break;
                        }
                    }
                }
            }
        }

        const tagElements = document.querySelectorAll('.tagItem');
        tagElements.forEach(tag => {
            const text = tag.textContent.trim();
            if (text && !info.tags.includes(text)) {
                info.tags.push(text);
            }
        });

        const zoomer = document.querySelector('.lbContainer-zoomer[data-src]');
        if (zoomer) {
            info.coverImage = zoomer.getAttribute('data-src');
        } else {
            const firstPost = document.querySelector('.message-inner');
            if (firstPost) {
                const firstImg = firstPost.querySelector('.bbImage');
                if (firstImg) {
                    // F95 lazy-loads post images: the real URL lives in
                    // data-src while src may be a placeholder. The scraper
                    // (core/scraper.py) prefers data-src for the same reason.
                    const lazySrc = typeof firstImg.getAttribute === 'function'
                        ? firstImg.getAttribute('data-src')
                        : '';
                    info.coverImage = lazySrc || firstImg.src || '';
                }
            }
        }

        const brWidget = document.querySelector('.br-widget.bratr-rating');
        if (brWidget) {
            const allStars = brWidget.querySelectorAll('a[data-rating-value]');
            let ratingValue = 0;
            allStars.forEach(star => {
                if (star.classList.contains('br-selected') || star.classList.contains('br-current')) {
                    ratingValue = parseInt(star.getAttribute('data-rating-value'));
                }
                const fractionalClass = [...star.classList].find(c => c.startsWith('br-fractional-'));
                if (fractionalClass) {
                    const pct = parseInt(fractionalClass.replace('br-fractional-', '')) / 100;
                    ratingValue = (parseInt(star.getAttribute('data-rating-value')) - 1) + pct;
                }
            });

            const voteEl = brWidget.querySelector('.bratr-vote-content');
            const votes = voteEl ? voteEl.textContent.trim() : '';

            info.rating = ratingValue > 0 ? `${ratingValue.toFixed(1)}/5 (${votes})` : '';
        }
    } catch (e) {
        console.error('wLib: Failed to extract some metadata', e);
    }

    return info;
}

function getPlayStatusToneClass(playStatus) {
    const key = String(playStatus || '').trim().toLowerCase();
    return WLIB_PLAY_STATUS_TONE_CLASS_MAP[key] || 'wlib-play-status--not-started';
}

function setActionButtonContent(button, label, iconName, variant) {
    const icon = createIconImage(iconName);
    const text = document.createElement('span');
    text.textContent = label;

    button.replaceChildren(icon, text);
    button.classList.toggle('is-open-action', variant === 'open');
    button.classList.toggle('is-success-action', variant === 'success');
}

function injectUI(gameInfo) {
    const container = document.createElement('div');
    container.id = WLIB_THREAD_ROOT_ID;
    container.className = 'wlib-add-container';
    container.setAttribute('data-state', 'expanded');

    const headerRow = document.createElement('div');
    headerRow.className = 'wlib-header-row';

    const brandEl = document.createElement('div');
    brandEl.className = 'wlib-brand';
    brandEl.appendChild(createLogoImage());

    const collapseBtn = document.createElement('button');
    collapseBtn.type = 'button';
    collapseBtn.className = 'wlib-collapse-btn';
    collapseBtn.setAttribute('aria-label', 'Collapse wLib widget');
    collapseBtn.appendChild(createIconImage('arrowDownLeft'));

    headerRow.appendChild(brandEl);
    headerRow.appendChild(collapseBtn);

    const body = document.createElement('div');
    body.className = 'wlib-widget-body';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'wlib-add-btn';
    const actionRow = document.createElement('div');
    actionRow.className = 'wlib-action-row';

    if (gameInfo.existsInWLib) {
        setActionButtonContent(btn, 'Open in wLib', 'externalLink', 'open');
    } else {
        setActionButtonContent(btn, 'Add to wLib', 'libraryPlus', 'default');
    }

    const libraryStatusBlock = document.createElement('div');
    libraryStatusBlock.className = 'wlib-library-status-block';

    if (gameInfo.existsInWLib) {
        const playStatus = gameInfo.wishlisted ? 'Wishlisted' : (gameInfo.playStatus || WLIB_DEFAULT_PLAY_STATUS);
        const dividerEl = document.createElement('div');
        dividerEl.className = 'wlib-status-divider';

        const statusLine = document.createElement('p');
        statusLine.className = 'wlib-library-status-line';
        statusLine.appendChild(document.createTextNode('Status: '));

        const statusValue = document.createElement('span');
        statusValue.className = `wlib-play-status ${getPlayStatusToneClass(playStatus)}`;
        statusValue.textContent = playStatus;

        statusLine.appendChild(statusValue);
        libraryStatusBlock.appendChild(dividerEl);
        libraryStatusBlock.appendChild(statusLine);
    }

    const statusEl = document.createElement('div');
    statusEl.className = 'wlib-status';
    statusEl.setAttribute('aria-live', 'polite');

    const collapsedToggle = document.createElement('button');
    collapsedToggle.type = 'button';
    collapsedToggle.className = 'wlib-collapsed-toggle';
    collapsedToggle.setAttribute('aria-label', 'Expand wLib widget');
    collapsedToggle.appendChild(createLogoImage());

    const setCollapsed = (collapsed) => {
        container.classList.toggle('is-collapsed', collapsed);
        container.setAttribute('data-state', collapsed ? 'collapsed' : 'expanded');
        collapseBtn.tabIndex = collapsed ? -1 : 0;
        collapsedToggle.tabIndex = collapsed ? 0 : -1;
    };

    collapseBtn.addEventListener('click', () => {
        setCollapsed(true);
    });

    collapsedToggle.addEventListener('click', () => {
        setCollapsed(false);
    });

    btn.addEventListener('click', () => {
        btn.disabled = true;
        statusEl.textContent = '';

        if (gameInfo.existsInWLib) {
            setActionButtonContent(btn, 'Opening...', 'externalLink', 'open');
            chrome.runtime.sendMessage({
                action: 'openWLib',
                ids: (gameInfo.links || []).map(link => `${link.source}:${link.id}`),
                url: gameInfo.url
            }, (response) => {
                if (chrome.runtime.lastError || !response?.success) {
                    statusEl.textContent = wlibConnectionStatusMessage();
                    btn.disabled = false;
                    setActionButtonContent(btn, 'Open in wLib', 'externalLink', 'open');
                    return;
                }
                statusEl.textContent = 'Brought wLib to front.';
                setTimeout(() => {
                    container.style.opacity = '0';
                    setTimeout(() => container.remove(), 300);
                }, 2000);
            });
        } else {
            setActionButtonContent(btn, 'Adding...', 'libraryPlus', 'default');

            chrome.runtime.sendMessage({
                action: 'addGameToWLib',
                payload: gameInfo
            }, (response) => {
                if (chrome.runtime.lastError) {
                    const err = chrome.runtime.lastError.message;
                    statusEl.textContent = wlibConnectionStatusMessage();
                    btn.disabled = false;
                    setActionButtonContent(btn, 'Retry', 'libraryPlus', 'default');
                    console.error('wLib Extension Error:', err);
                    return;
                }

                if (response && response.success) {
                    setActionButtonContent(btn, 'Added to wLib', 'libraryPlus', 'success');
                    statusEl.textContent = 'Game sent to wLib successfully.';
                    setTimeout(() => {
                        container.style.opacity = '0';
                        setTimeout(() => container.remove(), 300);
                    }, 3000);
                } else {
                    statusEl.textContent = wlibConnectionStatusMessage();
                    btn.disabled = false;
                    setActionButtonContent(btn, 'Retry', 'libraryPlus', 'default');
                }
            });
        }
    });

    if (!gameInfo.existsInWLib) {
        const wishlistBtn = document.createElement('button');
        wishlistBtn.type = 'button';
        wishlistBtn.className = 'wlib-add-btn wlib-wishlist-btn';
        wishlistBtn.textContent = 'Wishlist';
        wishlistBtn.addEventListener('click', () => {
            // An Add started mid-save would open the dialog for a game that is about to exist.
            wishlistBtn.disabled = true;
            btn.disabled = true;
            wishlistBtn.textContent = 'Saving...';
            statusEl.textContent = '';
            chrome.runtime.sendMessage({
                action: 'wishlistGameInWLib',
                payload: gameInfo,
            }, (response) => {
                // Reading lastError marks it handled; a missing response falls through to Retry.
                if (chrome.runtime.lastError) {
                    console.error('wLib Extension Error:', chrome.runtime.lastError.message);
                }
                if (response?.success) {
                    wishlistBtn.textContent = 'Wishlisted';
                    statusEl.textContent = 'Wishlisted';
                    setTimeout(() => {
                        container.style.opacity = '0';
                        setTimeout(() => container.remove(), 300);
                    }, 3000);
                } else if (response?.error_code === 'duplicate_url') {
                    wishlistBtn.textContent = 'Already in wLib';
                    statusEl.textContent = 'Already in wLib';
                } else {
                    wishlistBtn.disabled = false;
                    btn.disabled = false;
                    wishlistBtn.textContent = 'Retry';
                    statusEl.textContent = isExtensionContextInvalidated()
                        ? 'wLib extension updated. Refresh this page.'
                        : 'Make sure wLib is open';
                }
            });
        });
        actionRow.appendChild(wishlistBtn);
    }

    if (gameInfo.canAttachThread) {
        const attachBtn = document.createElement('button');
        attachBtn.type = 'button';
        attachBtn.className = 'wlib-add-btn wlib-wishlist-btn';
        attachBtn.textContent = 'Attach F95 thread';
        attachBtn.addEventListener('click', () => {
            attachBtn.disabled = true;
            attachBtn.textContent = 'Attaching…';
            chrome.runtime.sendMessage({action:'wishlistGameInWLib', payload:gameInfo}, response => {
                const error = chrome.runtime.lastError;
                if (!error && response?.attached) {
                    attachBtn.textContent = 'Thread attached';
                    statusEl.textContent = 'F95 thread attached to the existing game.';
                } else {
                    attachBtn.disabled = false;
                    attachBtn.textContent = 'Attach F95 thread';
                    statusEl.textContent = response?.error || wlibConnectionStatusMessage();
                }
            });
        });
        actionRow.appendChild(attachBtn);
    }

    container.appendChild(headerRow);
    actionRow.prepend(btn);
    if (gameInfo.appOutdated) {
        const outdatedNote = document.createElement('p');
        outdatedNote.className = 'wlib-outdated-warning';
        outdatedNote.textContent = 'Your wLib app is out of date. Some actions may not work.';
        body.appendChild(outdatedNote);
    }
    body.appendChild(actionRow);
    if (libraryStatusBlock.childElementCount) {
        body.appendChild(libraryStatusBlock);
    }
    body.appendChild(statusEl);
    container.appendChild(body);
    container.appendChild(collapsedToggle);

    setCollapsed(false);

    document.body.appendChild(container);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWLibExtension);
} else {
    initWLibExtension();
}
