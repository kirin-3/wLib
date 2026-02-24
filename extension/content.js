// Wait for the page to finish loading basic elements
function initWLibExtension() {
    // Only run on actual thread pages
    if (!window.location.pathname.startsWith('/threads/')) return;

    // Check if we already injected the button
    if (document.getElementById('wlib-extension-root')) return;

    // Try to extract game info
    const gameInfo = extractGameInfo();
    if (!gameInfo.title) return; // Not a valid game thread, probably

    // Check if the game is already in wLib
    fetch(`http://localhost:8183/api/check?url=${encodeURIComponent(gameInfo.url)}`)
        .then(response => response.json())
        .then(data => {
            gameInfo.existsInWLib = data.exists;
            injectUI(gameInfo);
        })
        .catch(err => {
            console.warn("wLib extension: Could not connect to background wLib service.", err);
            gameInfo.existsInWLib = false;
            injectUI(gameInfo);
        });
}

function extractGameInfo() {
    const info = {
        title: '',
        developer: '',
        version: '',
        engine: '',
        tags: [],
        rating: '',
        coverImage: '',
        url: window.location.href,
        id: ''
    };

    try {
        // 1. Thread ID from URL
        const threadMatch = window.location.pathname.match(/\/threads\/[^\/]+\.(\d+)/);
        if (threadMatch) info.id = threadMatch[1];

        // 2. Full Title (Parsing game name and version)
        // Usually: [Prefix] Game Name [Version] [Developer]
        const h1 = document.querySelector('h1.p-title-value');
        if (h1) {
            // Extract engine from the first labelLink (e.g. "Unity", "RPGM", "Ren'Py")
            const firstLabel = h1.querySelector('a.labelLink span');
            if (firstLabel) {
                info.engine = firstLabel.textContent.trim();
            }

            // Clone h1 and remove label/badge links before getting text content
            const h1Clone = h1.cloneNode(true);
            h1Clone.querySelectorAll('a.labelLink, span.label-append').forEach(el => el.remove());

            let fullTitleText = h1Clone.textContent.trim();
            // Remove the prefixes like [HTML] or [RPGM] at the start
            fullTitleText = fullTitleText.replace(/^\[[^\]]+\]\s*/, '');

            // Try to extract version e.g. [v1.0]
            const versionMatch = fullTitleText.match(/\[v?([^\]]+)\]/i);
            if (versionMatch) {
                info.version = versionMatch[1].trim();
                // Remove version tag from title
                fullTitleText = fullTitleText.replace(versionMatch[0], '');
            }

            // Extract developer e.g. [Developer Name]
            const devMatch = fullTitleText.match(/\[([^\]]+)\]$/);
            if (devMatch) {
                info.developer = devMatch[1].trim();
                fullTitleText = fullTitleText.replace(devMatch[0], '');
            }

            // Remove any remaining tags like [3D] [Sandbox] from the title text
            fullTitleText = fullTitleText.replace(/\[.*?\]/g, '').trim();

            info.title = fullTitleText;
        }

        // 2b. Fallback version extraction from first post body
        if (!info.version) {
            const firstPost = document.querySelector('.message-body .bbWrapper');
            if (firstPost) {
                // Try innerHTML first: handles <b>Version</b>\n: 1.0.7 pattern
                const htmlMatch = firstPost.innerHTML.match(/<b>\s*Version\s*<\/b>\s*:?\s*v?([^<\n]+)/i);
                if (htmlMatch) {
                    // Clean up: take first word-like token (e.g. "1.0.7" from "1.0.7 (fan translated)")
                    info.version = htmlMatch[1].trim().split(/\s/)[0];
                } else {
                    // Fallback: plain text search
                    const postText = firstPost.textContent;
                    const bodyVersionMatch = postText.match(/Version\s*:?\s*v?([^\s,;\n]+)/i);
                    if (bodyVersionMatch) {
                        info.version = bodyVersionMatch[1].trim();
                    }
                }
            }
        }

        // 3. Tags
        const tagElements = document.querySelectorAll('.tagItem');
        tagElements.forEach(tag => {
            const text = tag.textContent.trim();
            if (text && !info.tags.includes(text)) {
                info.tags.push(text);
            }
        });

        // 4. Cover Image — use the zoomer overlay's data-src (high-res cover)
        const zoomer = document.querySelector('.lbContainer-zoomer[data-src]');
        if (zoomer) {
            info.coverImage = zoomer.getAttribute('data-src');
        } else {
            // Fallback: first bbImage in the first post
            const firstPost = document.querySelector('.message-inner');
            if (firstPost) {
                const firstImg = firstPost.querySelector('.bbImage');
                if (firstImg) {
                    info.coverImage = firstImg.src;
                }
            }
        }

        // 5. Rating — computed from the br-widget star elements
        // Each fully selected star has class "br-selected"
        // The fractional star has class "br-fractional br-fractional-XX" where XX is the percentage
        const brWidget = document.querySelector('.br-widget.bratr-rating');
        if (brWidget) {
            const allStars = brWidget.querySelectorAll('a[data-rating-value]');
            let ratingValue = 0;
            allStars.forEach(star => {
                if (star.classList.contains('br-selected') || star.classList.contains('br-current')) {
                    ratingValue = parseInt(star.getAttribute('data-rating-value'));
                }
                // Check for fractional star e.g. "br-fractional-60" means 60% of that star
                const fractionalClass = [...star.classList].find(c => c.startsWith('br-fractional-'));
                if (fractionalClass) {
                    const pct = parseInt(fractionalClass.replace('br-fractional-', '')) / 100;
                    ratingValue = (parseInt(star.getAttribute('data-rating-value')) - 1) + pct;
                }
            });

            // Also grab vote count
            const voteEl = brWidget.querySelector('.bratr-vote-content');
            const votes = voteEl ? voteEl.textContent.trim() : '';

            info.rating = ratingValue > 0 ? `${ratingValue.toFixed(1)}/5 (${votes})` : '';
        }
    } catch (e) {
        console.error("wLib: Failed to extract some metadata", e);
    }

    return info;
}

function injectUI(gameInfo) {
    const container = document.createElement('div');
    container.id = 'wlib-extension-root';
    container.className = 'wlib-add-container';

    const titleEl = document.createElement('h3');
    titleEl.className = 'wlib-title';
    titleEl.textContent = gameInfo.title || 'Unknown Game';
    titleEl.title = gameInfo.title;

    const btn = document.createElement('button');
    btn.className = 'wlib-add-btn';

    if (gameInfo.existsInWLib) {
        btn.textContent = 'Open in wLib';
        btn.style.backgroundColor = '#16a34a'; // Green indicating already in library
    } else {
        btn.textContent = 'Add to wLib';
    }

    const statusEl = document.createElement('div');
    statusEl.className = 'wlib-status';

    btn.addEventListener('click', () => {
        btn.disabled = true;

        if (gameInfo.existsInWLib) {
            btn.textContent = 'Opening...';
            // Send open command
            chrome.runtime.sendMessage({
                action: "openWLib",
                url: gameInfo.url
            }, (response) => {
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
                action: "addGameToWLib",
                payload: gameInfo
            }, (response) => {
                if (chrome.runtime.lastError) {
                    const err = chrome.runtime.lastError.message;
                    statusEl.textContent = 'Error: Make sure wLib is open.';
                    btn.disabled = false;
                    btn.textContent = 'Retry';
                    console.error("wLib Extension Error:", err);
                    return;
                }

                if (response && response.success) {
                    btn.textContent = 'Added ✔';
                    btn.style.backgroundColor = '#16a34a'; // Green
                    statusEl.textContent = 'Game sent to wLib successfully.';
                    // Close the popup after a few seconds
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

    container.appendChild(titleEl);
    container.appendChild(btn);
    container.appendChild(statusEl);

    document.body.appendChild(container);
}

// Run on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWLibExtension);
} else {
    initWLibExtension();
}
