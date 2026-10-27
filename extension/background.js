// SPDX-License-Identifier: GPL-3.0-or-later
// wLib's /api/open needs this header (or an extension Origin). Web pages can't
// send it without a CORS preflight, which wLib rejects for non-extension origins.
const WLIB_HEADERS = { "X-wLib-Extension": "1" };

// The desktop app may be starting, busy, or gone; its HTTP server may accept
// a connection it never answers. Abort every request after a timeout so the
// content script always gets an answer and its buttons never stick on
// "Adding..." / "Opening..." (or never appear, for the pre-inject check).
const WLIB_REQUEST_TIMEOUT_MS = 10000;

// Resolves { response, data } with the JSON body already read, so the timeout
// also covers a server that sends headers and then stalls the body.
async function fetchWLibJson(url, options, timeoutMs = WLIB_REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, { ...(options || {}), signal: controller.signal });
        const data = await response.json();
        return { response, data };
    } finally {
        clearTimeout(timer);
    }
}

function readWLibResponse({ response, data }) {
    if (!response.ok || data.success === false) {
        throw new Error(data.error || `Request failed with status ${response.status}`);
    }
    return data;
}

// Minimum wLib app version this extension works with. Raise by hand only when
// the extension starts depending on app behavior older apps lack.
const MIN_APP_VERSION = "0.4.2";

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
        ...(Number.isSafeInteger(data?.gameId) ? { gameId: data.gameId } : {}),
        ...(data?.canAttachThread ? { canAttachThread: true } : {}),
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

function pageQuery(message) {
    let query = `url=${encodeURIComponent(message.url || '')}`;
    for (const id of Array.isArray(message.ids) ? message.ids : []) query += `&id=${encodeURIComponent(id)}`;
    return query;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === "saveThreadLinks") {
        fetchWLibJson("http://localhost:8183/api/thread-links", { method:"POST", headers:{"Content-Type":"application/json", ...WLIB_HEADERS}, body:JSON.stringify(message.payload) })
            .then(readWLibResponse).then(data => sendResponse({success:true,data}))
            .catch(error => sendResponse({success:false,error:error.message}));
        return true;
    }
    if (message.action === "checkGameInWLib") {
        fetchWLibJson(`http://localhost:8183/api/check?${pageQuery(message)}`)
            .then(({ response, data }) => {
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
        fetchWLibJson("http://localhost:8183/api/add", {
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
        fetchWLibJson("http://localhost:8183/api/wishlist", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...WLIB_HEADERS },
            body: JSON.stringify({
                ...message.payload,
                tags: Array.isArray(message.payload.tags)
                    ? message.payload.tags.join(', ')
                    : message.payload.tags,
            }),
        })
            .then(({ response, data }) => {
                sendResponse(response.ok && data.success
                    ? { success: true, data }
                    : { success: false, error: data.error, error_code: data.error_code, attached: data.attached === true });
            })
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }

    if (message.action === "openWLib") {
        fetchWLibJson(`http://localhost:8183/api/open?${pageQuery(message)}`, { headers: WLIB_HEADERS })
            .then(readWLibResponse)
            .then(data => sendResponse({ success: true, data }))
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }
});

// ---- Extension fetch fallback (bridge client) ----
// Background-only fetch proxy: allowlisted F95 URLs, credentialed GET,
// 25-second abort, 4 MiB streamed cap, off-site redirect guard. No tabs,
// windows, offscreen, or cookies API use.
const F95_THREAD_FETCH_RE = /^https:\/\/f95zone\.to\/threads\/\d+\/$/;
const F95_BATCH_FETCH_RE = /^https:\/\/f95zone\.to\/sam\/checker\.php\?threads=\d+(,\d+){0,99}$/;
const BRIDGE_URL = "ws://localhost:8183/api/bridge";
const BRIDGE_FETCH_TIMEOUT_MS = 25000;
const BRIDGE_MAX_BODY_BYTES = 4 * 1024 * 1024;
const BRIDGE_PING_INTERVAL_MS = 20000;
const BRIDGE_ALARM_NAME = "wlib-bridge";
const BRIDGE_BACKOFF_MIN_MS = 5000;
const BRIDGE_BACKOFF_MAX_MS = 60000;

function isBridgeUrlAllowed(url) {
    return F95_THREAD_FETCH_RE.test(url) || F95_BATCH_FETCH_RE.test(url);
}

async function bridgeFetchUrl(url, timeoutMs = BRIDGE_FETCH_TIMEOUT_MS) {
    if (!isBridgeUrlAllowed(url)) {
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
            if (new URL(finalUrl).hostname !== "f95zone.to") {
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
            for (const chunk of chunks) {
                if (typeof chunk === "string") {
                    body += chunk;
                } else if (chunk instanceof Uint8Array) {
                    body += new TextDecoder().decode(chunk, { stream: true });
                } else if (chunk && typeof chunk.byteLength === "number") {
                    body += new TextDecoder().decode(new Uint8Array(chunk.buffer || chunk), { stream: true });
                }
            }
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

// Bridge WebSocket client: persistent connection to the running app, hello
// with can_fetch, 20-second ping, 1-minute alarm reconnect, 5-60s backoff,
// console-only errors.
let bridgeSocket = null;
let bridgeBackoffMs = BRIDGE_BACKOFF_MIN_MS;
let bridgePingTimer = null;
let bridgeReconnectTimer = null;

async function bridgeGetCanFetch() {
    try {
        if (typeof chrome !== "undefined" && chrome.permissions && chrome.permissions.contains) {
            const origins = ["https://f95zone.to/*"];
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
            return String(chrome.runtime.getManifest().version || "1.1.2");
        }
    } catch (error) {
        console.warn("wLib extension: version read failed.", error);
    }
    return "1.1.2";
}

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

async function bridgeSendHello() {
    if (!bridgeSocket || bridgeSocket.readyState !== 1) {
        return;
    }
    try {
        const canFetch = await bridgeGetCanFetch();
        bridgeSocket.send(JSON.stringify({
            type: "hello",
            protocol: 1,
            extension_version: bridgeGetVersion(),
            can_fetch: canFetch,
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
        const canFetch = await bridgeGetCanFetch();
        bridgeSocket.send(JSON.stringify({ type: "status", can_fetch: canFetch }));
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
        const fetched = await bridgeFetchUrl(url);
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

function ensureBridge() {
    try {
        if (typeof WebSocket === "undefined") {
            return;
        }
        if (bridgeSocket && (bridgeSocket.readyState === 0 || bridgeSocket.readyState === 1)) {
            return;
        }
        let socket;
        try {
            socket = new WebSocket(BRIDGE_URL);
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
            try {
                bridgePingTimer = setInterval(() => {
                    try {
                        if (bridgeSocket && bridgeSocket.readyState === 1) {
                            bridgeSocket.send(JSON.stringify({ type: "ping" }));
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
            // pong and unknown types are ignored.
        };
        const handleClose = () => {
            bridgeClearPing();
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
        chrome.permissions.onAdded.addListener(() => bridgeSendStatus());
    }
    if (typeof chrome !== "undefined" && chrome.permissions && chrome.permissions.onRemoved) {
        chrome.permissions.onRemoved.addListener(() => bridgeSendStatus());
    }
} catch (_permissionListenerError) {}

// Optional sites are registered only while their permission is granted.
const WLIB_OPTIONAL_SITES = {
    dlsite: 'https://www.dlsite.com/*',
    vndb: 'https://vndb.org/*',
    ryuugames: 'https://www.ryuugames.com/*',
};
let siteRegistrationQueue = Promise.resolve();
async function syncSiteScripts() {
    const siteApi = globalThis.browser || chrome;
    if (!siteApi.scripting || !siteApi.permissions) return;
    const registered = await siteApi.scripting.getRegisteredContentScripts();
    for (const [site, origin] of Object.entries(WLIB_OPTIONAL_SITES)) {
        const id = `wlib-${site}`;
        const allowed = await siteApi.permissions.contains({origins:[origin]});
        const exists = registered.some(script => script.id === id);
        if (!allowed && exists) await siteApi.scripting.unregisterContentScripts({ids:[id]});
        if (allowed && !exists) {
            const files = ['store-links.js','site-widget.js',`sites/${site}.js`];
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
    void queueSiteSync();
    chrome.permissions.onAdded.addListener(queueSiteSync);
    chrome.permissions.onRemoved.addListener(queueSiteSync);
    chrome.runtime.onStartup?.addListener(queueSiteSync);
    chrome.runtime.onInstalled?.addListener(queueSiteSync);
}
chrome.action?.onClicked?.addListener(() => chrome.runtime.openOptionsPage());
