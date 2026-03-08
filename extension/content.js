const WLIB_UPDATES_PAGE_PATH = '/sam/latest_alpha/';
const WLIB_THREAD_ROOT_ID = 'wlib-extension-root';
const WLIB_TILE_BADGE_SELECTOR = '.wlib-library-badge';

const wlibTileCheckCache = new Map();
const wlibPendingTileChecks = new Set();

let wlibLatestAlphaObserver = null;
let wlibLatestAlphaScanTimer = 0;
let wlibLatestAlphaHashListenerAttached = false;

function getThreadIdentity(rawUrl = window.location.href) {
    try {
        const parsed = new URL(rawUrl, window.location.origin);
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
    return window.location.pathname === WLIB_UPDATES_PAGE_PATH;
}

function checkGameInWLib(url) {
    return new Promise((resolve) => {
        chrome.runtime.sendMessage({
            action: 'checkGameInWLib',
            url,
        }, (response) => {
            if (chrome.runtime.lastError) {
                console.warn('wLib extension: Could not connect to background wLib service.', chrome.runtime.lastError);
                resolve(false);
                return;
            }

            if (response && response.success && response.data) {
                resolve(Boolean(response.data.exists));
                return;
            }

            console.warn('wLib extension: Check request failed.', response?.error || response);
            resolve(false);
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

    gameInfo.existsInWLib = await checkGameInWLib(gameInfo.url);
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
        .then((existsInWLib) => {
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
        id: threadIdentity.id
    };

    try {
        const h1 = document.querySelector('h1.p-title-value');
        if (h1) {
            const firstLabel = h1.querySelector('a.labelLink span');
            if (firstLabel) {
                info.engine = firstLabel.textContent.trim();
            }

            const h1Clone = h1.cloneNode(true);
            h1Clone.querySelectorAll('a.labelLink, span.label-append').forEach(el => el.remove());

            let fullTitleText = h1Clone.textContent.trim();
            fullTitleText = fullTitleText.replace(/^xLibrary\s*-\s*/i, '');
            fullTitleText = fullTitleText.replace(/^wLib\s*-\s*/i, '');
            fullTitleText = fullTitleText.replace(/^\[[^\]]+\]\s*/, '');

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
                let found = false;
                for (const pattern of htmlPatterns) {
                    const htmlMatch = firstPost.innerHTML.match(pattern);
                    if (htmlMatch) {
                        info.version = htmlMatch[1].trim().split(/\s/)[0];
                        found = true;
                        break;
                    }
                }

                if (!found) {
                    const postText = firstPost.textContent;
                    const textPatterns = [
                        /(?:Current\s+)?Version\s*:?\s*v?([^\s,;\n]+)/i,
                        /Ver(?:\.|sion)?\s*:?\s*v?([^\s,;\n]+)/i,
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
                    info.coverImage = firstImg.src;
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

function injectUI(gameInfo) {
    const container = document.createElement('div');
    container.id = WLIB_THREAD_ROOT_ID;
    container.className = 'wlib-add-container';

    const headerRow = document.createElement('div');
    headerRow.className = 'wlib-header-row';

    const logoContainer = document.createElement('div');
    logoContainer.innerHTML = `
      <svg class="wlib-logo" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="wlibGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#d152d1" />
            <stop offset="100%" stop-color="#5a3968" />
          </linearGradient>
        </defs>
        <circle cx="16" cy="16" r="15" fill="url(#wlibGradient)" />
        <path d="M8 10l3.2 12h1.8l3-8.5 3 8.5h1.8L24 10h-2.4l-2 7-2.4-7h-1.8l-2.4 7-2-7z" fill="#ffffff" />
      </svg>`;

    const titleEl = document.createElement('h3');
    titleEl.className = 'wlib-title';
    titleEl.textContent = gameInfo.title || 'Unknown Game';
    titleEl.title = gameInfo.title;

    headerRow.appendChild(logoContainer.firstChild);
    headerRow.appendChild(titleEl);

    const btn = document.createElement('button');
    btn.className = 'wlib-add-btn';

    if (gameInfo.existsInWLib) {
        btn.textContent = 'Open in wLib';
        btn.style.backgroundColor = '#16a34a';
    } else {
        btn.textContent = 'Add to wLib';
    }

    const statusEl = document.createElement('div');
    statusEl.className = 'wlib-status';

    btn.addEventListener('click', () => {
        btn.disabled = true;

        if (gameInfo.existsInWLib) {
            btn.textContent = 'Opening...';
            chrome.runtime.sendMessage({
                action: 'openWLib',
                url: gameInfo.url
            }, () => {
                if (chrome.runtime.lastError) {
                    statusEl.textContent = 'Error: Make sure wLib is open.';
                    btn.disabled = false;
                    btn.textContent = 'Open in wLib';
                    return;
                }
                statusEl.textContent = 'Brought wLib to front.';
                setTimeout(() => {
                    container.style.opacity = '0';
                    setTimeout(() => container.remove(), 300);
                }, 2000);
            });
        } else {
            btn.textContent = 'Adding...';

            chrome.runtime.sendMessage({
                action: 'addGameToWLib',
                payload: gameInfo
            }, (response) => {
                if (chrome.runtime.lastError) {
                    const err = chrome.runtime.lastError.message;
                    statusEl.textContent = 'Error: Make sure wLib is open.';
                    btn.disabled = false;
                    btn.textContent = 'Retry';
                    console.error('wLib Extension Error:', err);
                    return;
                }

                if (response && response.success) {
                    btn.textContent = 'Added ✔';
                    btn.style.backgroundColor = '#16a34a';
                    statusEl.textContent = 'Game sent to wLib successfully.';
                    setTimeout(() => {
                        container.style.opacity = '0';
                        setTimeout(() => container.remove(), 300);
                    }, 3000);
                } else {
                    statusEl.textContent = 'Failed to add to database.';
                    btn.disabled = false;
                    btn.textContent = 'Retry';
                }
            });
        }
    });

    container.appendChild(headerRow);
    container.appendChild(btn);
    container.appendChild(statusEl);

    document.body.appendChild(container);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWLibExtension);
} else {
    initWLibExtension();
}
