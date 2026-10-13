// SPDX-License-Identifier: GPL-3.0-or-later
// wLib's /api/open needs this header (or an extension Origin). Web pages can't
// send it without a CORS preflight, which wLib rejects for non-extension origins.
const WLIB_HEADERS = { "X-wLib-Extension": "1" };

async function readWLibResponse(response) {
    const data = await response.json();
    if (!response.ok || data.success === false) {
        throw new Error(data.error || `Request failed with status ${response.status}`);
    }
    return data;
}

// Minimum wLib app version this extension works with. Raise by hand only when
// the extension starts depending on app behavior older apps lack.
const MIN_APP_VERSION = "0.3.8";

// Compare dot-separated numeric release components, treating missing parts as
// zero. A leading `v` and any `-suffix` (e.g. `-rc1`) are ignored, so
// `0.4.0-rc1` meets a `0.4.0` floor. Returns 1, 0 or -1.
function compareVersions(a, b) {
    const parts = (value) => String(value || "")
        .trim()
        .replace(/^[vV](?=\d)/, "")
        .split("-")[0]
        .split(".")
        .map((part) => {
            const num = parseInt(part, 10);
            return Number.isNaN(num) ? 0 : num;
        });
    const aParts = parts(a);
    const bParts = parts(b);
    const length = Math.max(aParts.length, bParts.length);
    for (let i = 0; i < length; i++) {
        const aNum = i < aParts.length ? aParts[i] : 0;
        const bNum = i < bParts.length ? bParts[i] : 0;
        if (aNum > bNum) return 1;
        if (aNum < bNum) return -1;
    }
    return 0;
}

function normalizeCheckPayload(data) {
    const payload = {
        exists: Boolean(data && data.exists),
        reachable: true,
    };

    if (payload.exists && data && typeof data.playStatus === 'string' && data.playStatus.trim()) {
        payload.playStatus = data.playStatus.trim();
    }
    if (payload.exists) {
        payload.wishlisted = Boolean(data.wishlisted);
    }

    const appVersion = data && typeof data.app_version === 'string'
        ? data.app_version.trim()
        : '';
    payload.appOutdated = !appVersion || compareVersions(appVersion, MIN_APP_VERSION) < 0;

    return payload;
}

// Reload the unpacked Chromium extension when the app has synced a newer
// version into the live folder. Firefox is skipped: reloading an installed
// add-on does not pick up the synced files. The marker limits this to one
// reload attempt per target version, so an extension loaded from another
// folder (whose version never changes) does not reload on every check.
function maybeReloadForNewExtensionVersion(data) {
    try {
        const reported = data && typeof data.extension_version === 'string'
            ? data.extension_version.trim()
            : '';
        if (!reported) {
            return;
        }

        const running = chrome.runtime.getManifest().version;
        if (compareVersions(reported, running) <= 0) {
            return;
        }

        let isChromium = false;
        try {
            isChromium = String(chrome.runtime.getURL('')).startsWith('chrome-extension://');
        } catch (_urlError) {
            return;
        }
        if (!isChromium) {
            return;
        }

        if (!chrome.storage || !chrome.storage.local) {
            return;
        }
        chrome.storage.local.get('reloadAttemptedFor', (stored) => {
            if (chrome.runtime.lastError) {
                return;
            }
            if (stored && stored.reloadAttemptedFor === reported) {
                return;
            }
            chrome.storage.local.set({ reloadAttemptedFor: reported }, () => {
                if (chrome.runtime.lastError) {
                    return;
                }
                try {
                    chrome.runtime.reload();
                } catch (reloadError) {
                    console.warn('wLib extension: reload failed.', reloadError);
                }
            });
        });
    } catch (error) {
        console.warn('wLib extension: reload check failed.', error);
    }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === "checkGameInWLib") {
        fetch(`http://localhost:8183/api/check?url=${encodeURIComponent(message.url)}`)
            .then(async (response) => {
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || `Request failed with status ${response.status}`);
                }
                sendResponse({ success: true, data: normalizeCheckPayload(data) });
                maybeReloadForNewExtensionVersion(data);
            })
            .catch(error => {
                console.error("wLib Check Error:", error);
                sendResponse({ success: false, error: error.message });
            });
        return true;
    }

    if (message.action === "addGameToWLib") {
        fetch("http://localhost:8183/api/add", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...WLIB_HEADERS
            },
            body: JSON.stringify(message.payload)
        })
            .then(readWLibResponse)
            .then(data => {
                console.log("wLib Response:", data);
                sendResponse({ success: true, data });
            })
            .catch(error => {
                console.error("wLib Connection Error:", error);
                sendResponse({ success: false, error: error.message });
            });

        // Return true to indicate we will send a response asynchronously
        return true;
    }

    if (message.action === "wishlistGameInWLib") {
        fetch("http://localhost:8183/api/wishlist", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...WLIB_HEADERS },
            body: JSON.stringify({
                ...message.payload,
                tags: Array.isArray(message.payload.tags)
                    ? message.payload.tags.join(', ')
                    : message.payload.tags,
            }),
        })
            .then(async response => {
                const data = await response.json();
                sendResponse(response.ok && data.success
                    ? { success: true, data }
                    : { success: false, error: data.error, error_code: data.error_code });
            })
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }

    if (message.action === "openWLib") {
        fetch(`http://localhost:8183/api/open?url=${encodeURIComponent(message.url)}`, { headers: WLIB_HEADERS })
            .then(readWLibResponse)
            .then(data => sendResponse({ success: true, data }))
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }
});
