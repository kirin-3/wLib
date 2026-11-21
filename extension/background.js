// SPDX-License-Identifier: GPL-3.0-or-later
// wLib serves only an extension the user allowed in the app. This install's
// random token goes out with every request in the X-wLib-Extension header
// (and as ?token= on the bridge socket); wLib asks the user once to allow it.
// Web pages can't send the header without a CORS preflight, which wLib rejects.
const PAIRING_TOKEN_KEY = "wlibPairingToken";
const PAIRING_TOKEN_RE = /^[0-9a-f]{64}$/;
let pairingTokenPromise = null;

function pairingToken() {
    if (!pairingTokenPromise) {
        pairingTokenPromise = (async () => {
            const stored = await chrome.storage.local.get(PAIRING_TOKEN_KEY);
            const saved = stored ? stored[PAIRING_TOKEN_KEY] : "";
            if (typeof saved === "string" && PAIRING_TOKEN_RE.test(saved)) {
                return saved;
            }
            const bytes = crypto.getRandomValues(new Uint8Array(32));
            const token = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
            await chrome.storage.local.set({ [PAIRING_TOKEN_KEY]: token });
            return token;
        })();
        // A failed read must not stick: the next request tries again.
        pairingTokenPromise.catch(() => { pairingTokenPromise = null; });
    }
    return pairingTokenPromise;
}

// The short code wLib shows in its "allow this extension" question.
async function pairingCode() {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(await pairingToken()));
    const hex = Array.from(new Uint8Array(digest).slice(0, 4), (byte) => byte.toString(16).padStart(2, "0")).join("").toUpperCase();
    return `${hex.slice(0, 4)}-${hex.slice(4)}`;
}

const PAIRING_NOTES = {
    pairing_required: "Allow this browser extension in the wLib app, then reload this page.",
    pairing_denied: "wLib refused this browser extension. Restart wLib to ask again.",
};

// The desktop app may be starting, busy, or gone; its HTTP server may accept
// a connection it never answers. Abort every request after a timeout so the
// content script always gets an answer and its buttons never stick on
// "Adding..." / "Opening..." (or never appear, for the pre-inject check).
const WLIB_REQUEST_TIMEOUT_MS = 10000;

// Resolves { response, data } with the JSON body already read, so the timeout
// also covers a server that sends headers and then stalls the body.
async function fetchWLibJson(url, options, timeoutMs = WLIB_REQUEST_TIMEOUT_MS) {
    // Without a token wLib answers "not allowed", which is still an answer.
    const token = await pairingToken().catch(() => "");
    const headers = { ...((options && options.headers) || {}), "X-wLib-Extension": token || "1" };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        let response;
        try {
            response = await fetch(url, { ...(options || {}), headers, signal: controller.signal });
        } catch (error) {
            // Only fetch's own TypeError means "no connection"; a TypeError
            // from our code later in the chain is a bug and must stay visible.
            if (error?.name === "TypeError") error.wlibOffline = true;
            throw error;
        }
        const data = await response.json();
        return { response, data };
    } finally {
        clearTimeout(timer);
    }
}

class WLibServerError extends Error {
    constructor(status, data) {
        const serverText = typeof data?.error === "string" ? data.error.trim() : "";
        super(serverText ? `HTTP ${status}: ${serverText}` : `HTTP ${status}`);
        this.name = "WLibServerError";
        this.status = status;
        this.serverText = serverText;
        this.error_code = data?.error_code;
        this.attached = data?.attached === true;
    }
}

function readWLibResponse({ response, data }) {
    if (!response.ok || data?.success === false) {
        throw new WLibServerError(response.status, data);
    }
    return data;
}

function wlibFailure(action, error) {
    let failure = "offline";
    let userText = "Make sure wLib is open. If it is, reload the wLib extension in your browser.";
    switch (error?.name) {
        case "AbortError":
            failure = "timeout";
            userText = "wLib did not answer. Try again.";
            break;
        case "SyntaxError":
            failure = "foreign";
            userText = "Another program answered on port 8183, not wLib.";
            break;
        case "WLibServerError":
            failure = "server";
            userText = error.serverText || "wLib could not do this. Check the wLib log.";
            break;
    }
    const detail = `${error?.name || "Error"}: ${error?.message ?? String(error)}`;
    const level = error?.wlibOffline === true ? "debug" : "warn";
    console[level](`wLib ${action} (${failure}):`, detail);
    return { success: false, failure, error: userText, detail };
}

// Minimum wLib app version this extension works with. Raise by hand only when
// the extension starts depending on app behavior older apps lack.
const MIN_APP_VERSION = "0.4.8";
// LewdCorner pages need the first app release with LC thread identity (after 0.4.7).
const MIN_LC_APP_VERSION = "0.4.8";

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

function normalizeCheckPayload(data, minimumVersion = MIN_APP_VERSION) {
    const payload = {
        ...(Number.isSafeInteger(data?.gameId) ? { gameId: data.gameId } : {}),
        ...(data?.canAttachThread ? { canAttachThread: true } : {}),
        ...(data?.attach_lc_available ? { attach_lc_available: true } : {}),
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
    payload.appOutdated = !appVersion || compareVersions(appVersion, minimumVersion) < 0;
    const pairingNote = data && PAIRING_NOTES[data.error_code];
    if (pairingNote) {
        payload.pairingNote = pairingNote;
    }

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

function pageQuery(message) {
    let query = `url=${encodeURIComponent(message.url || '')}`;
    for (const id of Array.isArray(message.ids) ? message.ids : []) query += `&id=${encodeURIComponent(id)}`;
    return query;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "dl-owned?") {
        const entry = resolveEntryFor(sender, message.url);
        sendResponse(entry ? {owned:true, host:entry.host, buzzAllowed:entry.buzzAllowed, site:entry.site} : {owned:false});
        return;
    }
    if (["dl-progress", "dl-result", "lc-exit-continue"].includes(message.type)) {
        const entry = resolveWindows.get(sender.tab?.id);
        if (!entry || (sender.frameId != null && sender.frameId !== 0)) return;
        if (message.type === "lc-exit-continue") {
            if (entry.site === "lc" && resolveSite(sender.url || "") === "lc" && !entry.lcHandoff) {
                finishLcHandoff(entry, message.url, true);
            }
        } else if (message.type === "dl-progress" && message.stage === "needs_user") {
            resolveNeedsUser(entry);
        } else if (message.type === "dl-result") {
            finishResolve(entry, message);
        }
        return;
    }
    if (message.type === "dl-buzz-permission") {
        const entry = resolveWindows.get(sender.tab?.id);
        if (!entry) { sendResponse({allowed:false}); return; }
        const origin = safeBuzzOrigin(message.url);
        if (!origin) { sendResponse({allowed:false}); return; }
        Promise.resolve(chrome.permissions.contains({origins:[origin]})).then(allowed => sendResponse({allowed})).catch(() => sendResponse({allowed:false}));
        return true;
    }
    if (message.action === "pairingCode") {
        pairingCode().then(code => sendResponse({ code })).catch(() => sendResponse({ code: "" }));
        return true;
    }
    if (message.action === "saveThreadLinks") {
        fetchWLibJson("http://localhost:8183/api/thread-links", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(message.payload) })
            .then(readWLibResponse).then(data => sendResponse({success:true,data}))
            .catch(error => sendResponse(wlibFailure(message.action, error)));
        return true;
    }
    if (message.action === "checkGameInWLib") {
        fetchWLibJson(`http://localhost:8183/api/check?${pageQuery(message)}`)
            .then(readWLibResponse)
            .then(data => {
                const minimum = /^https:\/\/(?:www\.)?lewdcorner\.com\//i.test(message.url)
                    ? MIN_LC_APP_VERSION : MIN_APP_VERSION;
                sendResponse({ success: true, data: normalizeCheckPayload(data, minimum) });
                maybeReloadForNewExtensionVersion(data);
            })
            .catch(error => sendResponse(wlibFailure(message.action, error)));
        return true;
    }

    if (message.action === "checkLewdCornerThreads") {
        const threadIds = Array.isArray(message.threadIds) ? message.threadIds.slice(0, 250) : [];
        const query = new URLSearchParams({ batch: "1" });
        for (const id of new Set(threadIds)) {
            if (typeof id === "string" && /^[1-9][0-9]*$/.test(id)) {
                query.append("url", `https://lewdcorner.com/threads/${id}/`);
            }
        }
        fetchWLibJson(`http://localhost:8183/api/check?${query}`)
            .then(result => {
                const data = readWLibResponse(result);
                // Pairing errors arrive with HTTP 200 so version checks still work.
                maybeReloadForNewExtensionVersion(data);
                if (data.error_code) {
                    throw new WLibServerError(result.response.status, data);
                }
                sendResponse({ success: true, matches: data.matches || {} });
            })
            .catch(error => sendResponse(wlibFailure(message.action, error)));
        return true;
    }

    if (message.action === "addGameToWLib") {
        fetchWLibJson("http://localhost:8183/api/add", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(message.payload)
        })
            .then(readWLibResponse)
            .then(data => {
                console.log("wLib Response:", data);
                sendResponse({ success: true, data });
            })
            .catch(error => sendResponse(wlibFailure(message.action, error)));

        // Return true to indicate we will send a response asynchronously
        return true;
    }

    if (message.action === "wishlistGameInWLib") {
        fetchWLibJson("http://localhost:8183/api/wishlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ...message.payload,
                tags: Array.isArray(message.payload.tags)
                    ? message.payload.tags.join(', ')
                    : message.payload.tags,
            }),
        })
            .then(({ response, data }) => {
                if (!response.ok || !data?.success) {
                    throw new WLibServerError(response.status, data);
                }
                sendResponse({ success: true, data });
            })
            .catch(error => sendResponse({
                ...wlibFailure(message.action, error),
                error_code: error?.error_code,
                attached: error?.attached === true,
            }));
        return true;
    }

    if (message.action === "openWLib") {
        fetchWLibJson(`http://localhost:8183/api/open?${pageQuery(message)}`)
            .then(readWLibResponse)
            .then(data => sendResponse({ success: true, data }))
            .catch(error => sendResponse(wlibFailure(message.action, error)));
        return true;
    }
});

// ---- Extension fetch fallback (bridge client) ----
// Fetch proxy: allowlisted F95 and LC URLs, credentialed GET, 25-second abort,
// 4 MiB streamed cap, off-site redirect guard. Tabs are only queried for an
// open F95 tab to repeat a guest fetch in; no windows, offscreen, or cookies
// API use.
const F95_THREAD_FETCH_RE = /^https:\/\/f95zone\.to\/threads\/\d+\/$/;
const F95_BATCH_FETCH_RE = /^https:\/\/f95zone\.to\/sam\/checker\.php\?threads=\d+(,\d+){0,99}$/;
// The signed-in user's Watched games and Bookmarked games lists, title order, pages 1-200.
const F95_WATCHED_LIST_RE = /^https:\/\/f95zone\.to\/watched\/threads\?tab=games&unread=0&sort=title&page=([1-9]\d{0,2})$/;
const F95_BOOKMARK_LIST_RE = /^https:\/\/f95zone\.to\/watched\/threads\?tab=bookmarks&bm=games&sort=title&page=([1-9]\d{0,2})$/;
const F95_LIST_MAX_PAGE = 200;
const LC_FETCH_RE = /^https:\/\/lewdcorner\.com\/(?:(?:threads\/\d+\/)|(?:latest-updates\/api\?page=(?:[1-9]|10)&per_page=90&cat=Games&sort=date))$/;
const LC_ORIGIN = "https://lewdcorner.com/*";
// LC attachment images only (covers and screenshots), never proxied or masked links.
const LC_IMAGE_RE = /^https:\/\/lewdcorner\.com\/attachments\/[^/?#\s]+\.\d+\/$/;
const BRIDGE_MAX_IMAGE_BYTES = 20 * 1024 * 1024;
// Image data per bridge message; base64 makes it about 5.4 MiB, under wLib's 8 MiB message cap.
const BRIDGE_IMAGE_CHUNK_BYTES = 4 * 1024 * 1024;
const BRIDGE_IMAGE_TIMEOUT_MS = 55000;
const BRIDGE_URL = "ws://localhost:8183/api/bridge";
const BRIDGE_FETCH_TIMEOUT_MS = 25000;
const BRIDGE_MAX_BODY_BYTES = 4 * 1024 * 1024;
const BRIDGE_PING_INTERVAL_MS = 20000;
const BRIDGE_ALARM_NAME = "wlib-bridge";
const BRIDGE_BACKOFF_MIN_MS = 5000;
const BRIDGE_BACKOFF_MAX_MS = 60000;

function isF95ListUrlAllowed(url) {
    const match = F95_WATCHED_LIST_RE.exec(url) || F95_BOOKMARK_LIST_RE.exec(url);
    return !!match && Number(match[1]) <= F95_LIST_MAX_PAGE;
}

function isBridgeUrlAllowed(url) {
    return F95_THREAD_FETCH_RE.test(url) || F95_BATCH_FETCH_RE.test(url) || isF95ListUrlAllowed(url);
}

function isLcBridgeUrlAllowed(url) {
    return typeof url === "string" && url === url.trim() && LC_FETCH_RE.test(url);
}

function isSiteBridgeUrlAllowed(url, site) {
    if (site === "lc") {
        return isLcBridgeUrlAllowed(url);
    }
    return site === "f95" && isBridgeUrlAllowed(url);
}

async function bridgeFetchUrl(url, timeoutMs = BRIDGE_FETCH_TIMEOUT_MS, site = "f95") {
    const allowed = isSiteBridgeUrlAllowed(url, site);
    if (!allowed) {
        return { ok: false, error: "refused" };
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, {
            method: "GET",
            credentials: "include",
            redirect: "follow",
            cache: "no-store",
            signal: controller.signal,
        });
        let finalUrl = url;
        try {
            finalUrl = response.url || url;
            if (new URL(finalUrl).hostname !== (site === "lc" ? "lewdcorner.com" : "f95zone.to")) {
                return { ok: false, error: "offsite_redirect" };
            }
        } catch (_urlError) {
            return { ok: false, error: "offsite_redirect" };
        }
        // Streamed 4 MiB cap when a reader is available; otherwise read whole
        // body and check its byte length.
        if (response.body && typeof response.body.getReader === "function") {
            const reader = response.body.getReader();
            const chunks = [];
            let total = 0;
            try {
                for (;;) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    const size = value ? value.byteLength || value.length || 0 : 0;
                    total += size;
                    if (total > BRIDGE_MAX_BODY_BYTES) {
                        try { controller.abort(); } catch (_abortError) {}
                        try { await reader.cancel(); } catch (_cancelError) {}
                        return { ok: false, error: "too_large" };
                    }
                    chunks.push(value);
                }
            } finally {
                try { reader.releaseLock(); } catch (_lockError) {}
            }
            // Reassemble as text (tests stub getReader with string chunks).
            let body = "";
            const decoder = new TextDecoder();
            for (const chunk of chunks) {
                if (typeof chunk === "string") {
                    body += chunk;
                } else if (chunk instanceof Uint8Array) {
                    body += decoder.decode(chunk, { stream: true });
                } else if (chunk && typeof chunk.byteLength === "number") {
                    body += decoder.decode(new Uint8Array(chunk.buffer || chunk), { stream: true });
                }
            }
            body += decoder.decode();
            if (body.length > BRIDGE_MAX_BODY_BYTES) {
                return { ok: false, error: "too_large" };
            }
            return { ok: true, status: response.status || 200, final_url: finalUrl, body };
        }
        const body = await response.text();
        const byteLength = typeof TextEncoder !== "undefined"
            ? new TextEncoder().encode(body).length
            : body.length;
        if (byteLength > BRIDGE_MAX_BODY_BYTES) {
            return { ok: false, error: "too_large" };
        }
        return { ok: true, status: response.status || 200, final_url: finalUrl, body };
    } catch (error) {
        if (error && error.name === "AbortError") {
            return { ok: false, error: "timeout" };
        }
        return { ok: false, error: "network" };
    } finally {
        clearTimeout(timer);
    }
}

// Fetches one LC attachment image in the background context. The body is
// read only for an image answer of at most 20 MiB that stayed on LC.
async function bridgeFetchImage(url, timeoutMs = BRIDGE_IMAGE_TIMEOUT_MS) {
    if (typeof url !== "string" || /\s/.test(url) || !LC_IMAGE_RE.test(url)) {
        return { ok: false, error: "refused" };
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, {
            method: "GET",
            credentials: "include",
            redirect: "follow",
            cache: "no-store",
            signal: controller.signal,
        });
        const finalUrl = response.url || url;
        try {
            if (new URL(finalUrl).hostname !== "lewdcorner.com") {
                try { controller.abort(); } catch (_abortError) {}
                return { ok: false, error: "offsite_redirect" };
            }
        } catch (_urlError) {
            return { ok: false, error: "offsite_redirect" };
        }
        const status = response.status || 200;
        const contentType = String(response.headers?.get?.("content-type") || "").trim().slice(0, 200);
        const head = { ok: true, status, content_type: contentType, final_url: finalUrl };
        if (!/^image\//i.test(contentType)) {
            try { controller.abort(); } catch (_abortError) {}
            return { ...head, bytes: null };
        }
        const declared = Number(response.headers?.get?.("content-length") || 0);
        if (declared > BRIDGE_MAX_IMAGE_BYTES) {
            try { controller.abort(); } catch (_abortError) {}
            return { ok: false, error: "too_large" };
        }
        const chunks = [];
        let total = 0;
        if (response.body && typeof response.body.getReader === "function") {
            const reader = response.body.getReader();
            try {
                for (;;) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value || []);
                    total += bytes.byteLength;
                    if (total > BRIDGE_MAX_IMAGE_BYTES) {
                        try { controller.abort(); } catch (_abortError) {}
                        try { await reader.cancel(); } catch (_cancelError) {}
                        return { ok: false, error: "too_large" };
                    }
                    chunks.push(bytes);
                }
            } finally {
                try { reader.releaseLock(); } catch (_lockError) {}
            }
        } else {
            const bytes = new Uint8Array(await response.arrayBuffer());
            total = bytes.byteLength;
            if (total > BRIDGE_MAX_IMAGE_BYTES) {
                return { ok: false, error: "too_large" };
            }
            chunks.push(bytes);
        }
        const bytes = new Uint8Array(total);
        let offset = 0;
        for (const chunk of chunks) {
            bytes.set(chunk, offset);
            offset += chunk.byteLength;
        }
        return { ...head, bytes };
    } catch (error) {
        if (error && error.name === "AbortError") {
            return { ok: false, error: "timeout" };
        }
        return { ok: false, error: "network" };
    } finally {
        clearTimeout(timer);
    }
}

function bridgeBase64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i += 0x8000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

// The answer as fetch_image_chunk messages: the first carries the result,
// each carries at most 4 MiB of image data, and the last one says so.
function bridgeImageMessages(id, fetched) {
    if (!fetched.ok) {
        return [{ type: "fetch_image_chunk", id, index: 0, last: true, ok: false, error: fetched.error }];
    }
    const head = { ok: true, status: fetched.status, content_type: fetched.content_type, final_url: fetched.final_url };
    const bytes = fetched.bytes || new Uint8Array(0);
    const messages = [];
    let index = 0;
    for (let offset = 0; offset < bytes.byteLength || index === 0; offset += BRIDGE_IMAGE_CHUNK_BYTES) {
        const part = bytes.subarray(offset, offset + BRIDGE_IMAGE_CHUNK_BYTES);
        messages.push({
            type: "fetch_image_chunk", id, index, ...(index === 0 ? head : {}),
            data: bridgeBase64(part), last: offset + BRIDGE_IMAGE_CHUNK_BYTES >= bytes.byteLength,
        });
        index += 1;
    }
    return messages;
}

async function bridgeHandleFetchImage(message) {
    const id = message && typeof message.id === "string" ? message.id : "";
    if (!id) {
        return;
    }
    let fetched;
    try {
        fetched = await bridgeGetCanFetch(LC_ORIGIN)
            ? await bridgeFetchImage(message.url)
            : { ok: false, error: "refused" };
    } catch (error) {
        console.warn("wLib extension: image fetch failed.", error);
        fetched = { ok: false, error: "network" };
    }
    try {
        for (const part of bridgeImageMessages(id, fetched)) {
            if (!bridgeSocket || bridgeSocket.readyState !== 1) {
                return;
            }
            bridgeSocket.send(JSON.stringify(part));
        }
    } catch (error) {
        console.warn("wLib extension: image result send failed.", error);
    }
}

// Firefox can send no site cookies with background-page fetches (always for
// F95; for LC when the login lives in a container tab, as with Zen
// workspaces, or under strict cookie settings), so the site answers as a
// guest and Cloudflare challenges them without the browser's clearance.
// The same request from inside an open tab of that site is first-party and
// carries the tab's login and clearance. Once a background fetch came back as
// a guest or challenge page, later fetches for that site use a tab first
// while one is open.
const F95_GUEST_PAGE_RE = /\bdata-logged-in=["']false["']/;
// The managed challenge loads /h/ scripts; signed-out pages only carry the
// passive /scripts/jsd/ probe.
const F95_CHALLENGE_PAGE_RE = /<title>\s*Just a moment|\/cdn-cgi\/challenge-platform\/h\//i;
let bridgePreferTabFetch = false;
let bridgePreferLcTabFetch = false;

const BRIDGE_TAB_SITES = {
    f95: { host: "f95zone.to", tabs: "https://f95zone.to/*" },
    lc: { host: "lewdcorner.com", tabs: "https://lewdcorner.com/*" },
};

function bridgeNeedsBrowserSession(body) {
    if (typeof body !== "string") {
        return false;
    }
    return F95_GUEST_PAGE_RE.test(body.slice(0, 4096)) || F95_CHALLENGE_PAGE_RE.test(body.slice(0, 65536));
}

// LC thread pages are XenForo pages like F95's. The LC update feed answers a
// guest with an HTML page, a bare JSON string or a JSON "error" field.
function lcNeedsBrowserSession(url, body) {
    if (typeof body !== "string") {
        return false;
    }
    if (!url.includes("/latest-updates/api?")) {
        return bridgeNeedsBrowserSession(body);
    }
    const text = body.replace(/^[\ufeff\s]+/, "");
    if (text.startsWith("<")) {
        return true;
    }
    try {
        const payload = JSON.parse(text);
        if (typeof payload === "string") {
            return payload.length > 0;
        }
        return !!payload && typeof payload.error === "string" && payload.error.length > 0;
    } catch (_error) {
        return false;
    }
}

// Runs inside the F95 tab as a content script. It must stay self-contained.
async function bridgeTabFetch(url, maxBytes, timeoutMs, host = "f95zone.to") {
    // Firefox's content.fetch runs as the page; Chrome content scripts already do.
    const hasPageFetch = typeof content !== "undefined" && content && typeof content.fetch === "function";
    const pageFetch = async (target, init) => {
        if (hasPageFetch) {
            try {
                return await content.fetch(target, init);
            } catch (pageError) {
                if (init.signal.aborted) {
                    throw pageError;
                }
                // Fall back to the content script's own fetch.
            }
        }
        return fetch(target, init);
    };
    // The abort also stops the body read, so a timed-out request does not
    // keep running after the app gave up on it and started the next one.
    const controller = new AbortController();
    let timer = null;
    const timeout = new Promise((resolve) => {
        timer = setTimeout(() => {
            controller.abort();
            resolve({ ok: false, error: "timeout" });
        }, timeoutMs);
    });
    const request = (async () => {
        try {
            const response = await pageFetch(url, {
                method: "GET",
                credentials: "include",
                redirect: "follow",
                cache: "no-store",
                signal: controller.signal,
            });
            const finalUrl = response.url || url;
            if (new URL(finalUrl).hostname !== host) {
                return { ok: false, error: "offsite_redirect" };
            }
            const body = await response.text();
            if (new TextEncoder().encode(body).length > maxBytes) {
                return { ok: false, error: "too_large" };
            }
            return { ok: true, status: response.status || 200, final_url: finalUrl, body };
        } catch (_error) {
            return { ok: false, error: "network" };
        }
    })();
    try {
        return await Promise.race([request, timeout]);
    } finally {
        clearTimeout(timer);
    }
}

// Returns null when no tab of the site can run the fetch.
async function bridgeFetchViaTab(url, timeoutMs = BRIDGE_FETCH_TIMEOUT_MS, site = "f95") {
    const tabSite = BRIDGE_TAB_SITES[site];
    if (!tabSite || !isSiteBridgeUrlAllowed(url, site)) {
        return { ok: false, error: "refused" };
    }
    try {
        if (typeof chrome === "undefined" || !chrome.tabs || !chrome.scripting) {
            return null;
        }
        const tabs = await chrome.tabs.query({ url: tabSite.tabs });
        const tab = (tabs || []).find((item) => item && typeof item.id === "number" && !item.discarded);
        if (!tab) {
            return null;
        }
        const injected = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: bridgeTabFetch,
            args: [url, BRIDGE_MAX_BODY_BYTES, timeoutMs, tabSite.host],
        });
        const result = injected && injected[0] ? injected[0].result : null;
        return result && typeof result.ok === "boolean" ? result : null;
    } catch (error) {
        console.warn("wLib extension: tab fetch failed.", error);
        return null;
    }
}

// Smallest budget worth starting a second attempt with.
const BRIDGE_MIN_ATTEMPT_MS = 2000;

function bridgePrefersTab(site) {
    return site === "lc" ? bridgePreferLcTabFetch : bridgePreferTabFetch;
}

function bridgeSetPrefersTab(site, value) {
    if (site === "lc") {
        bridgePreferLcTabFetch = value;
    } else {
        bridgePreferTabFetch = value;
    }
}

// All attempts share one BRIDGE_FETCH_TIMEOUT_MS budget, so the answer
// arrives before the app stops waiting and starts its next request.
// Each answer says how it was made, for the app log: "via" is "background"
// or "tab", and "tab_retry" says why a signed-out background answer was kept
// ("no_tab", "failed" or "no_time").
async function bridgeFetchPreferringLogin(url, site = "f95") {
    const deadline = Date.now() + BRIDGE_FETCH_TIMEOUT_MS;
    const remaining = () => deadline - Date.now();
    const needsSession = (body) => (
        site === "lc" ? lcNeedsBrowserSession(url, body) : bridgeNeedsBrowserSession(body)
    );
    const preferTab = bridgePrefersTab(site);
    if (preferTab) {
        const viaTab = await bridgeFetchViaTab(url, remaining(), site);
        if (viaTab) {
            return { ...viaTab, via: "tab" };
        }
        if (remaining() < BRIDGE_MIN_ATTEMPT_MS) {
            return { ok: false, error: "timeout", via: "tab" };
        }
    }
    const fetched = { ...await bridgeFetchUrl(url, remaining(), site), via: "background" };
    if (preferTab || !fetched.ok || !needsSession(fetched.body)) {
        return fetched;
    }
    if (remaining() < BRIDGE_MIN_ATTEMPT_MS) {
        return { ...fetched, tab_retry: "no_time" };
    }
    const viaTab = await bridgeFetchViaTab(url, remaining(), site);
    if (!viaTab) {
        return { ...fetched, tab_retry: "no_tab" };
    }
    if (!viaTab.ok) {
        console.warn(`wLib extension: ${site} tab fetch failed: ${viaTab.error}`);
        return { ...fetched, tab_retry: "failed" };
    }
    bridgeSetPrefersTab(site, true);
    return { ...viaTab, via: "tab" };
}

// Bridge WebSocket client: persistent connection to the running app, hello
// with can_fetch, 20-second ping, 1-minute alarm reconnect, 5-60s backoff,
// console-only errors.
let bridgeSocket = null;
let bridgeBackoffMs = BRIDGE_BACKOFF_MIN_MS;
let bridgePingTimer = null;
let bridgeReconnectTimer = null;

async function bridgeGetCanFetch(origin = "https://f95zone.to/*") {
    try {
        if (typeof chrome !== "undefined" && chrome.permissions && chrome.permissions.contains) {
            const origins = [origin];
            const maybe = chrome.permissions.contains({ origins });
            const result = typeof maybe?.then === "function" ? await maybe : maybe;
            // Callback-style chrome.permissions.contains is handled by callers
            // that pass a fake returning a boolean; treat truthy as granted.
            return !!result;
        }
    } catch (error) {
        console.warn("wLib extension: permission check failed.", error);
    }
    return false;
}

function bridgeGetVersion() {
    try {
        if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.getManifest) {
            return String(chrome.runtime.getManifest().version || "1.2.14");
        }
    } catch (error) {
        console.warn("wLib extension: version read failed.", error);
    }
    return "1.2.14";
}

// Firefox suspends an idle event page after 30 seconds, and WebSocket traffic
// does not count as activity, so the bridge would drop every half minute. An
// extension event does count: while connected, each ping also writes a
// storage key whose onChanged event lands in this background page.
const BRIDGE_KEEPALIVE_KEY = "wlibBridgeKeepalive";

function bridgeNeedsKeepAlive() {
    return typeof browser !== "undefined" && !!browser.runtime
        && typeof browser.runtime.getBrowserInfo === "function";
}

function bridgeKeepAlive() {
    try {
        if (!bridgeNeedsKeepAlive() || !chrome.storage || !chrome.storage.local) {
            return;
        }
        const pending = chrome.storage.local.set({ [BRIDGE_KEEPALIVE_KEY]: Date.now() });
        if (pending && typeof pending.catch === "function") {
            pending.catch(() => {});
        }
    } catch (error) {
        console.warn("wLib extension: keepalive failed.", error);
    }
}

try {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.onChanged) {
        // The listener only has to exist for the event to reach this page.
        chrome.storage.onChanged.addListener(() => {});
    }
} catch (_keepaliveListenerError) {}

function bridgeClearPing() {
    if (bridgePingTimer !== null) {
        try { clearInterval(bridgePingTimer); } catch (_clearError) {}
        bridgePingTimer = null;
    }
}

function bridgeScheduleReconnect() {
    if (bridgeReconnectTimer !== null) {
        return;
    }
    const delay = bridgeBackoffMs;
    bridgeBackoffMs = Math.min(bridgeBackoffMs * 2, BRIDGE_BACKOFF_MAX_MS);
    try {
        bridgeReconnectTimer = setTimeout(() => {
            bridgeReconnectTimer = null;
            ensureBridge();
        }, delay);
    } catch (error) {
        console.warn("wLib extension: reconnect scheduling failed.", error);
        bridgeReconnectTimer = null;
    }
}

async function bridgeCapabilities() {
    const [canFetch, canFetchLc, webRequest] = await Promise.all([
        bridgeGetCanFetch(), bridgeGetCanFetch(LC_ORIGIN), bridgeGetCanWatchLcRedirects(),
    ]);
    return {can_fetch:canFetch, can_fetch_lc:canFetchLc, can_resolve_lc:canFetchLc && webRequest, can_fetch_lc_images:canFetchLc};
}

async function bridgeGetCanWatchLcRedirects() {
    return !!await chrome.permissions.contains({permissions:['webRequest']});
}

async function bridgeSendHello() {
    if (!bridgeSocket || bridgeSocket.readyState !== 1) {
        return;
    }
    try {
        const capabilities = await bridgeCapabilities();
        bridgeSocket.send(JSON.stringify({
            type: "hello",
            protocol: 2,
            can_resolve: true,
            can_fetch_lists: true,
            extension_version: bridgeGetVersion(),
            ...capabilities,
        }));
    } catch (error) {
        console.warn("wLib extension: hello failed.", error);
    }
}

async function bridgeSendStatus() {
    if (!bridgeSocket || bridgeSocket.readyState !== 1) {
        return;
    }
    try {
        const capabilities = await bridgeCapabilities();
        bridgeSocket.send(JSON.stringify({ type: "status", ...capabilities }));
    } catch (error) {
        console.warn("wLib extension: status update failed.", error);
    }
}

async function bridgeHandleFetchMessage(message) {
    const id = message && typeof message.id === "string" ? message.id : "";
    const url = message && typeof message.url === "string" ? message.url : "";
    if (!id || !url) {
        return;
    }
    let result;
    try {
        let fetched;
        if (message.site === "lc" && await bridgeGetCanFetch(LC_ORIGIN)) {
            fetched = await bridgeFetchPreferringLogin(url, "lc");
        } else if (message.site === undefined || message.site === "f95") {
            fetched = await bridgeFetchPreferringLogin(url);
        } else {
            fetched = { ok: false, error: "refused" };
        }
        if (fetched.ok) {
            result = {
                type: "fetch_result",
                id,
                ok: true,
                status: fetched.status,
                final_url: fetched.final_url,
                body: fetched.body,
            };
        } else {
            result = { type: "fetch_result", id, ok: false, error: fetched.error };
        }
        if (fetched.via) {
            result.via = fetched.via;
        }
        if (fetched.tab_retry) {
            result.tab_retry = fetched.tab_retry;
        }
    } catch (error) {
        console.warn("wLib extension: bridge fetch failed.", error);
        result = { type: "fetch_result", id, ok: false, error: "network" };
    }
    try {
        if (bridgeSocket && bridgeSocket.readyState === 1) {
            bridgeSocket.send(JSON.stringify(result));
        }
    } catch (error) {
        console.warn("wLib extension: fetch result send failed.", error);
    }
}

let bridgeToken = null;
let bridgeTokenLoading = false;
// Set by a failed token read for one connect only, so the next connect
// reads again instead of staying unpaired until the worker restarts.
let bridgeTokenReadFailed = false;

function ensureBridge() {
    try {
        if (typeof WebSocket === "undefined") {
            return;
        }
        if (bridgeSocket && (bridgeSocket.readyState === 0 || bridgeSocket.readyState === 1)) {
            return;
        }
        if (bridgeToken === null && !bridgeTokenReadFailed) {
            // Read the token once, then come back; a failed read connects
            // without it (wLib answers "not allowed") and the next connect
            // tries the read again.
            if (!bridgeTokenLoading) {
                bridgeTokenLoading = true;
                pairingToken().then((token) => {
                    bridgeToken = token || null;
                    bridgeTokenReadFailed = !token;
                }, () => {
                    bridgeTokenReadFailed = true;
                }).then(() => {
                    bridgeTokenLoading = false;
                    ensureBridge();
                });
            }
            return;
        }
        const token = bridgeToken || "";
        bridgeTokenReadFailed = false;
        let socket;
        try {
            socket = new WebSocket(token ? `${BRIDGE_URL}?token=${token}` : BRIDGE_URL);
        } catch (error) {
            console.warn("wLib extension: bridge connect failed.", error);
            bridgeScheduleReconnect();
            return;
        }
        bridgeSocket = socket;
        socket.onopen = () => {
            bridgeBackoffMs = BRIDGE_BACKOFF_MIN_MS;
            if (bridgeReconnectTimer !== null) {
                try { clearTimeout(bridgeReconnectTimer); } catch (_clearError) {}
                bridgeReconnectTimer = null;
            }
            bridgeSendHello();
            bridgeClearPing();
            bridgeKeepAlive();
            try {
                bridgePingTimer = setInterval(() => {
                    try {
                        if (bridgeSocket && bridgeSocket.readyState === 1) {
                            bridgeSocket.send(JSON.stringify({ type: "ping" }));
                            bridgeKeepAlive();
                        }
                    } catch (error) {
                        console.warn("wLib extension: ping failed.", error);
                    }
                }, BRIDGE_PING_INTERVAL_MS);
            } catch (error) {
                console.warn("wLib extension: ping scheduling failed.", error);
            }
        };
        socket.onmessage = (event) => {
            let message;
            try {
                message = JSON.parse(typeof event.data === "string" ? event.data : "");
            } catch (_parseError) {
                return;
            }
            if (!message || typeof message.type !== "string") {
                return;
            }
            if (message.type === "fetch") {
                bridgeHandleFetchMessage(message);
            }
            if (message.type === "fetch_image") bridgeHandleFetchImage(message);
            if (message.type === "resolve") bridgeHandleResolve(message);
            if (message.type === "resolve_cancel") {
                resolveOpening.get(message.id)?.abort();
                for (const entry of resolveWindows.values()) if (entry.id === message.id) finishResolve(entry,{ok:false,error:'cancelled'});
            }
            // pong and unknown types are ignored.
        };
        const handleClose = () => {
            bridgeClearPing();
            for (const entry of [...resolveWindows.values()]) finishResolve(entry, {ok:false,error:"disconnected"});
            if (bridgeSocket === socket) {
                bridgeSocket = null;
            }
            bridgeScheduleReconnect();
        };
        socket.onclose = handleClose;
        socket.onerror = () => {
            // Errors stay console-only; onclose drives the reconnect.
            try { console.warn("wLib extension: bridge socket error."); } catch (_logError) {}
        };
    } catch (error) {
        console.warn("wLib extension: bridge ensure failed.", error);
    }
}

try {
    ensureBridge();
} catch (_startupError) {}

try {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onStartup) {
        chrome.runtime.onStartup.addListener(() => ensureBridge());
    }
} catch (_startupListenerError) {}

try {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onInstalled) {
        chrome.runtime.onInstalled.addListener(() => ensureBridge());
    }
} catch (_installedListenerError) {}

try {
    if (typeof chrome !== "undefined" && chrome.alarms && chrome.alarms.create) {
        chrome.alarms.create(BRIDGE_ALARM_NAME, { periodInMinutes: 1 });
    }
    if (typeof chrome !== "undefined" && chrome.alarms && chrome.alarms.onAlarm) {
        chrome.alarms.onAlarm.addListener((alarm) => {
            if (alarm && alarm.name === BRIDGE_ALARM_NAME) {
                ensureBridge();
            }
        });
    }
} catch (_alarmError) {}

try {
    if (typeof chrome !== "undefined" && chrome.permissions && chrome.permissions.onAdded) {
        chrome.permissions.onAdded.addListener(onBridgePermissionChange);
    }
    if (typeof chrome !== "undefined" && chrome.permissions && chrome.permissions.onRemoved) {
        chrome.permissions.onRemoved.addListener(onBridgePermissionChange);
    }
} catch (_permissionListenerError) {}

async function onBridgePermissionChange() {
    try { syncLcRedirectListener(await bridgeGetCanWatchLcRedirects()); }
    catch (_) { syncLcRedirectListener(false); }
    await bridgeSendStatus();
}

// Optional sites are registered only while their permission is granted.
const WLIB_OPTIONAL_SITES = {
    dlsite: 'https://www.dlsite.com/*',
    vndb: 'https://vndb.org/*',
    ryuugames: 'https://www.ryuugames.com/*',
    lewdcorner: 'https://lewdcorner.com/*',
};
let siteRegistrationQueue = Promise.resolve();
async function syncSiteScripts() {
    const siteApi = globalThis.browser || chrome;
    if (!siteApi.scripting || !siteApi.permissions) return;
    const registered = await siteApi.scripting.getRegisteredContentScripts();
    for (let i = 0; i < buzzOrigins.length; i++) {
        const id = `wlib-buzz-resolve-${i}`;
        const origin = buzzOrigins[i];
        const allowed = await siteApi.permissions.contains({origins:[origin]});
        const exists = registered.some(script=>script.id === id);
        if (allowed && !exists) await siteApi.scripting.registerContentScripts([{id,matches:[origin],js:['bzzhr-resolve.js'],runAt:'document_start'}]);
        if (!allowed && exists) await siteApi.scripting.unregisterContentScripts({ids:[id]});
    }

    const lcExitId = 'wlib-lc-exit-resolve';
    const lcAllowed = await siteApi.permissions.contains({origins:[LC_ORIGIN]});
    const lcExitExists = registered.some(script => script.id === lcExitId);
    if (lcAllowed && !lcExitExists) {
        await siteApi.scripting.registerContentScripts([{
            id:lcExitId, matches:['https://lewdcorner.com/masked/out*'], js:['lc-exit-resolve.js'], runAt:'document_start',
        }]);
    } else if (!lcAllowed && lcExitExists) {
        await siteApi.scripting.unregisterContentScripts({ids:[lcExitId]});
    }

    for (const [site, origin] of Object.entries(WLIB_OPTIONAL_SITES)) {
        const id = `wlib-${site}`;
        const allowed = await siteApi.permissions.contains({origins:[origin]});
        const existing = registered.find(script => script.id === id);
        const files = ['store-links.js','widget-placement.js','site-widget.js',`sites/${site}.js`];
        // Browsers may report registered paths with a leading slash or as full extension URLs.
        const registeredFiles = (existing?.js || []).map(file => String(file).replace(/^[a-z-]+:\/\/[^/]+\//i,'').replace(/^\//,''));
        const stale = existing && JSON.stringify(registeredFiles) !== JSON.stringify(files);
        const exists = Boolean(existing) && !stale;
        if (stale) await siteApi.scripting.unregisterContentScripts({ids:[id]});
        if (!allowed && exists) await siteApi.scripting.unregisterContentScripts({ids:[id]});
        if (allowed && !exists) {
            await siteApi.scripting.registerContentScripts([{
                id, matches:[origin], js:files, css:['content.css'], runAt:'document_idle',
            }]);
            // Runtime registration covers future navigations; also handle any
            // already-open pages immediately after the user's permission grant.
            if (siteApi.tabs?.query) for (const tab of await siteApi.tabs.query({url:[origin]})) {
                if (!Number.isInteger(tab.id)) continue;
                try {
                    await siteApi.scripting.insertCSS({target:{tabId:tab.id},files:['content.css']});
                    await siteApi.scripting.executeScript({target:{tabId:tab.id},files});
                } catch (error) { console.warn('wLib page closed or permission changed:',error); }
            }
        }
    }
}
function queueSiteSync() {
    siteRegistrationQueue = siteRegistrationQueue.then(syncSiteScripts).catch(error => console.warn('wLib site permissions:',error));
    return siteRegistrationQueue;
}
if (chrome.permissions && chrome.scripting) {
    void Promise.resolve().then(queueSiteSync);
    chrome.permissions.onAdded.addListener(queueSiteSync);
    chrome.permissions.onRemoved.addListener(queueSiteSync);
    chrome.runtime.onStartup?.addListener(queueSiteSync);
    chrome.runtime.onInstalled?.addListener(queueSiteSync);
}
chrome.action?.onClicked?.addListener(() => chrome.runtime.openOptionsPage());


// Resolve windows are the sole source of authority for content-script actions.
const resolveWindows = new Map();
const resolveOpening = new Map();
const RESOLVE_STORAGE_KEY = 'wlibResolveWindows';
const buzzOrigins = ['https://bzzhr.to/*','https://www.bzzhr.to/*','https://buzzheavier.com/*','https://www.buzzheavier.com/*'];
function safeBuzzOrigin(value) {
    try {
        const url = new URL(value);
        const origin = `${url.protocol}//${url.hostname}/*`;
        return buzzOrigins.includes(origin) ? origin : '';
    } catch (_) { return ''; }
}
function saveResolveWindows() {
    try { const p = chrome.storage?.session?.set({[RESOLVE_STORAGE_KEY]: [...resolveWindows.values()].map(({tabId,windowId,id})=>({tabId,windowId,id}))}); p?.catch?.(()=>{}); } catch (_) {}
}
function sameUrl(a, b) {
    try { return new URL(a).href === new URL(b).href; } catch (_) { return false; }
}
// Ownership follows the tab that actually loaded the requested /dl/ URL. Some
// browsers (Zen, Firefox) load the URL in a different tab from the one the new
// window reports, so the created window's tab id alone cannot be trusted.
// Zen can also load the same URL a second time as a tab in the user's window.
// Only the first tab to ask does the work; every other copy is closed.
const resolveFinishedUrls = new Map();
// Tabs the browser created while a resolve was opening or running. Only these
// can be a late browser copy; a tab the user opens or navigates is never one.
const resolveSpawnedTabs = new Set();
function forgetSpawnedTabsWhenIdle() {
    if (!resolveOpening.size && !resolveWindows.size) resolveSpawnedTabs.clear();
}
function closeTab(tabId) {
    try { const p = chrome.tabs?.remove(tabId); p?.catch?.(()=>{}); } catch (_) {}
}
function resolveEntryFor(sender, url) {
    const tabId = sender?.tab?.id;
    if (!Number.isInteger(tabId) || (sender.frameId != null && sender.frameId !== 0)) return null;
    const known = resolveWindows.get(tabId);
    if (known) {
        known.bound = true;
        return known;
    }
    if (typeof url !== 'string') return null;
    for (const [finishedUrl, finished] of resolveFinishedUrls) {
        if (finished.expires < Date.now()) resolveFinishedUrls.delete(finishedUrl);
        else if (finished.tabs.has(tabId) && sameUrl(finishedUrl, url)) { closeTab(tabId); return null; }
    }
    for (const entry of new Set(resolveWindows.values())) {
        if (entry.finished || !sameUrl(entry.url, url)) continue;
        // For LC, a matching URL outside the window we opened does not confer ownership.
        if (entry.site === 'lc' && sender.tab.windowId !== entry.windowId) continue;
        if (entry.bound) {
            entry.duplicates.add(tabId);
            closeTab(tabId);
            return null;
        }
        resolveWindows.delete(entry.tabId);
        entry.tabId = tabId;
        entry.tabWindowId = sender.tab.windowId;
        entry.bound = true;
        resolveWindows.set(tabId, entry);
        saveResolveWindows();
        return entry;
    }
    return null;
}
function sendResolveMessage(message) {
    try { if (bridgeSocket?.readyState === 1) bridgeSocket.send(JSON.stringify(message)); } catch (_) {}
}
function finishResolve(entry, result) {
    if (!entry || entry.finished) return;
    entry.finished = true;
    clearTimeout(entry.timer);
    resolveWindows.delete(entry.tabId);
    saveResolveWindows();
    const answer = {type:'resolve_result', id:entry.id, ok:result.ok === true};
    for (const field of ['url','host','error','needs_permission','share_url']) {
        if (typeof result[field] === 'string') answer[field] = result[field];
    }
    if (answer.ok && typeof navigator !== 'undefined' && typeof navigator.userAgent === 'string') {
        answer.user_agent = navigator.userAgent;
    }
    sendResolveMessage(answer);
    // A late copy of this URL (Zen and Firefox can load it twice) is closed
    // on sight, but only briefly and only after a success: after a failure
    // the user's own "Open in browser" loads the same URL and must stay.
    if (answer.ok && entry.site !== "lc") {
        resolveFinishedUrls.set(entry.url, {expires: Date.now() + 10000, tabs: new Set(resolveSpawnedTabs)});
    }
    forgetSpawnedTabsWhenIdle();
    for (const tabId of entry.duplicates) closeTab(tabId);
    if (entry.tabWindowId != null && entry.tabWindowId !== entry.windowId) {
        try { const p = chrome.tabs.remove(entry.tabId); p?.catch?.(()=>{}); } catch (_) {}
    }
    try { const p = chrome.windows.remove(entry.windowId); p?.catch?.(()=>{}); } catch (_) {}
}
function resolveNeedsUser(entry) {
    if (entry.finished || entry.needsUser) return;
    entry.needsUser = true;
    clearTimeout(entry.timer);
    entry.timer = setTimeout(()=>finishResolve(entry,{ok:false,error:'timed out'}),300000);
    sendResolveMessage({type:'resolve_progress',id:entry.id,stage:'needs_user'});
    try { const p = chrome.windows.update(entry.tabWindowId ?? entry.windowId,{state:'normal',focused:true}); p?.catch?.(()=>{}); } catch (_) {}
    try { const p = chrome.tabs.update(entry.tabId,{active:true}); p?.catch?.(()=>{}); } catch (_) {}
}
function resolveSite(url) {
    if (url !== url.trim() || /[\r\n\t]/.test(url)) return '';
    if (/^https:\/\/f95zone\.to\/dl\/\d+\/\?[^#]+$/.test(url)) return 'f95';
    if (/^https:\/\/lewdcorner\.com\/masked\/out\?[^#]+$/.test(url)) return 'lc';
    return '';
}
function lcMirror(value) {
    try {
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
            || url.hostname === 'lewdcorner.com' || url.hostname.endsWith('.lewdcorner.com')) return '';
        return url.href;
    } catch (_) { return ''; }
}
function finishLcHandoff(entry, value, navigate) {
    const url = lcMirror(value);
    if (!url || entry.finished || entry.lcHandoff) return;
    const origin = safeBuzzOrigin(url);
    if (!origin) {
        finishResolve(entry, {ok:true, url, host:entry.host});
    } else if (!entry.buzzAllowed.includes(origin)) {
        finishResolve(entry, {ok:true, url, host:'buzzheavier', needs_permission:'buzzheavier'});
    } else {
        entry.lcHandoff = true;
        entry.url = url;
        entry.host = 'buzzheavier';
        if (navigate) {
            Promise.resolve(chrome.tabs.update(entry.tabId, {url})).catch(() =>
                finishResolve(entry, {ok:false, error:'could not open Buzzheavier share page'}));
        }
    }
}
function onLcRedirect(details) {
    const entry = resolveWindows.get(details.tabId);
    if (!entry || entry.site !== 'lc' || details.type !== 'main_frame'
        || !/^https:\/\/lewdcorner\.com\/masked\/continue(?:\?|$)/.test(details.url)) return;
    finishLcHandoff(entry, details.redirectUrl, false);
}
let lcRedirectEvent;
function syncLcRedirectListener(allowed = true) {
    const event = allowed ? chrome.webRequest?.onBeforeRedirect : undefined;
    if (event === lcRedirectEvent) return;
    try {
        lcRedirectEvent?.removeListener(onLcRedirect);
        lcRedirectEvent = event;
        event?.addListener(onLcRedirect, {
            urls:['https://lewdcorner.com/masked/continue*'], types:['main_frame'],
        });
    } catch (_) { lcRedirectEvent = undefined; }
}
syncLcRedirectListener();
async function bridgeHandleResolve(message) {
    const id = typeof message.id === 'string' ? message.id : '';
    const url = typeof message.url === 'string' ? message.url : '';
    const site = resolveSite(url);
    if (!id || !site) {
        if (id) sendResolveMessage({type:'resolve_result',id,ok:false,error:'invalid download URL'});
        return;
    }
    let win;
    const socket = bridgeSocket;
    const opening = new AbortController();
    resolveOpening.set(id, opening);
    try {
        await resolveRecovery;
        if (site === 'lc') {
            await siteRegistrationQueue;
            if (!await bridgeGetCanFetch(LC_ORIGIN) || !await bridgeGetCanWatchLcRedirects()) {
                sendResolveMessage({type:'resolve_result',id,ok:false,error:'Turn on LewdCorner downloads in the wLib extension settings'});
                return;
            }
            syncLcRedirectListener();
        }
        const buzzChecks = await Promise.all(buzzOrigins.map(origin => Promise.resolve(chrome.permissions.contains({origins:[origin]}))));
        const buzzAllowed = buzzOrigins.filter((_, index) => buzzChecks[index]);
        if (opening.signal.aborted || !socket || bridgeSocket !== socket || socket.readyState !== 1) return;
        // Firefox-based browsers may refuse `focused` or `state` on create.
        for (const options of [{url,state:'minimized',focused:false},{url,state:'minimized'},{url}]) {
            try { win = await chrome.windows.create(options); break; } catch (_) {}
        }
        if (!win) throw new Error('window unavailable');
        if (win.state !== 'minimized') { try { await chrome.windows.update(win.id,{state:'minimized'}); } catch (_) {} }
        if (opening.signal.aborted || !socket || bridgeSocket !== socket || socket.readyState !== 1) {
            await chrome.windows.remove(win.id);
            return;
        }
        const tabId = win.tabs?.[0]?.id;
        if (!Number.isInteger(tabId) || !Number.isInteger(win.id)) throw new Error('window unavailable');
        const entry = {id,url,site,host:message.host,buzzAllowed,tabId,windowId:win.id,tabWindowId:win.id,bound:false,duplicates:new Set(),finished:false,needsUser:false};
        resolveWindows.set(tabId,entry);
        entry.timer = setTimeout(()=>finishResolve(entry,{ok:false,error:'timed out'}),30000);
        saveResolveWindows();
        sendResolveMessage({type:'resolve_progress',id,stage:'opened'});
    } catch (_) {
        if (win?.id != null) { try { await chrome.windows.remove(win.id); } catch (_) {} }
        sendResolveMessage({type:'resolve_result',id,ok:false,error:'could not open resolve window'});
    } finally {
        resolveOpening.delete(id);
        forgetSpawnedTabsWhenIdle();
    }
}
chrome.windows?.onRemoved?.addListener(windowId => {
    for (const entry of [...resolveWindows.values()]) if ((entry.tabWindowId ?? entry.windowId) === windowId) finishResolve(entry,{ok:false,error:'cancelled'});
});
chrome.tabs?.onCreated?.addListener(tab => {
    if (Number.isInteger(tab?.id) && (resolveOpening.size || resolveWindows.size)) resolveSpawnedTabs.add(tab.id);
});
chrome.tabs?.onRemoved?.addListener(tabId => {
    resolveSpawnedTabs.delete(tabId);
    const entry = resolveWindows.get(tabId);
    if (entry?.bound) finishResolve(entry,{ok:false,error:'cancelled'});
});
// A worker restart loses the bridge request; close any windows it left behind.
const resolveRecovery = (async () => {
    try {
        const stored = await chrome.storage?.session?.get(RESOLVE_STORAGE_KEY);
        for (const entry of stored?.[RESOLVE_STORAGE_KEY] || []) {
            try { await chrome.windows.remove(entry.windowId); } catch (_) {}
        }
        await chrome.storage?.session?.remove(RESOLVE_STORAGE_KEY);
    } catch (_) {}
})();
