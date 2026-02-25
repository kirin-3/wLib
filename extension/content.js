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
            // Remove xLibrary or wLib prefixes if they exist
            fullTitleText = fullTitleText.replace(/^xLibrary\s*-\s*/i, '');
            fullTitleText = fullTitleText.replace(/^wLib\s*-\s*/i, '');

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

    // Header Row (Logo + Title)
    const headerRow = document.createElement('div');
    headerRow.className = 'wlib-header-row';

    // Inject SVG directly to bypass any CSP image loading restrictions
    const logoContainer = document.createElement('div');
    logoContainer.innerHTML = `
      <svg class="wlib-logo" viewBox="0 0 500 500" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <radialGradient cx="51.277325" cy="53.078426" r="39.810619" fx="51.277325" fy="53.078426" id="wlibGradient" gradientUnits="userSpaceOnUse" gradientTransform="matrix(5.4883497,-2.0392683,1.96781,5.29602,-9.3512351,-17.75545)">
            <stop style="stop-color:#d152d1;stop-opacity:1" offset="0" />
            <stop style="stop-color:#5a3968;stop-opacity:1" offset="1" />
          </radialGradient>
        </defs>
        <circle style="fill:url(#wlibGradient)" cx="250" cy="250" r="240" />
        <g transform="matrix(3.2,0,0,3.2,-790,-90)">
          <path style="fill:#ffffff" d="m 265.46586,138.94695 c -0.14661,-0.38205 0.32981,-1.06481 1.0542,-1.51081 0.32872,-0.20238 1.81979,-0.74354 3.31349,-1.20258 3.83783,-1.17942 4.50193,-1.47267 5.29123,-2.33645 0.77175,-0.84458 0.70904,-0.70985 2.91987,-6.273 1.92302,-4.8389 2.36896,-5.90728 5.32782,-12.76432 1.24611,-2.88782 2.67657,-6.43196 3.1788,-7.87587 0.50223,-1.44391 1.73464,-4.90657 2.7387,-7.694805 1.00405,-2.788236 2.27712,-6.413849 2.82905,-8.056917 0.55192,-1.643068 1.36043,-3.890008 1.79668,-4.993201 0.43625,-1.103194 1.23751,-3.303003 1.78058,-4.888467 0.54308,-1.585463 1.40946,-4.023303 1.92529,-5.417421 0.51584,-1.394118 1.28933,-3.512453 1.71886,-4.707412 2.59289,-7.213379 3.39421,-9.116352 4.66791,-11.085288 0.98888,-1.528664 2.7424,-2.907936 4.57083,-3.595315 1.54021,-0.579021 3.91323,-0.774906 5.36261,-0.442663 1.39756,0.320367 3.7367,1.481552 4.70958,2.337911 0.8912,0.784468 2.32219,2.785846 4.09283,5.724237 1.14878,1.906403 4.86123,7.875319 5.85567,9.414824 2.74201,4.244902 4.15006,6.626771 4.15006,7.02024 0,0.411735 -2.31085,2.787344 -4.1309,4.246671 -4.18941,3.35908 -9.9288,6.577059 -12.826,7.19132 l -0.57149,0.121167 -0.28012,-1.232782 c -0.71642,-3.152931 -0.72365,-3.565986 -0.10713,-6.124028 0.68623,-2.847307 0.91028,-6.764296 0.53723,-9.392288 -0.25261,-1.77962 -0.56768,-2.282935 -1.42907,-2.282935 -0.95527,0 -0.9988,0.20386 -1.01857,4.769933 -0.0153,3.526463 -0.0789,4.479796 -0.40059,6.002798 -1.01668,4.813015 -3.21265,8.83176 -8.7042,15.92919 -5.86657,7.582121 -8.86833,12.077561 -9.4323,14.125821 -0.24693,0.89684 -0.26121,3.0529 -0.0257,3.87423 0.0967,0.33707 0.50075,1.15128 0.89795,1.80935 0.90094,1.49264 1.24006,2.44408 1.38888,3.89666 l 0.11652,1.13733 -1.19323,0.29973 c -2.20918,0.55493 -4.55338,1.49811 -5.91621,2.38038 -2.17008,1.40486 -4.87479,4.27845 -6.81689,7.24252 -2.48838,3.79782 -2.49126,3.80151 -3.24814,4.17712 -0.64997,0.32255 -1.21225,0.35333 -7.35996,0.40284 -5.8843,0.0474 -6.66875,0.021 -6.76418,-0.22772 z m 55.60606,-51.186257 c 2.42079,-1.221524 4.46005,-2.565857 6.60848,-4.356482 3.40313,-2.836362 3.8467,-3.460434 3.15819,-4.44342 -0.50672,-0.723432 -1.31323,-0.544746 -2.35025,0.520705 -1.52883,1.570751 -4.99622,4.11782 -6.52772,4.795121 -0.52026,0.230085 -0.99489,0.497545 -1.05472,0.594355 -0.0598,0.09681 -0.64213,0.474281 -1.294,0.838825 -1.07683,0.6022 -1.52738,1.115546 -1.52738,1.740277 0,0.09762 0.20779,0.385284 0.46175,0.639245 0.54522,0.545215 0.88029,0.501616 2.52565,-0.328626 z M 307.39315,63.938262 c 1.68778,-2.759438 3.24177,-3.546324 5.97004,-3.023031 1.28843,0.247124 1.39935,0.242634 1.82447,-0.07385 0.32762,-0.243904 0.45263,-0.506586 0.45263,-0.951119 0,-0.551578 -0.0871,-0.657365 -0.85496,-1.038297 -1.88496,-0.935136 -4.75048,-0.681251 -6.75041,0.598083 -1.53582,0.982454 -2.98448,2.978203 -2.98563,4.11317 -8.5e-4,0.833129 0.38742,1.235049 1.1931,1.235049 0.55078,0 0.68702,-0.101818 1.15076,-0.860007 z"/>
        </g>
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

    container.appendChild(headerRow);
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
