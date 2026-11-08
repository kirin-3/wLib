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
const MIN_APP_VERSION = "0.4.6";

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
    if (message.type === "dl-owned?") {
        const entry = resolveEntryFor(sender, message.url);
        sendResponse(entry ? {owned:true, host:entry.host, buzzAllowed:entry.buzzAllowed} : {owned:false});
        return;
    }
    if (message.type === "dl-progress" || message.type === "dl-result") {
        const entry = resolveWindows.get(sender.tab?.id);
        if (!entry) return;
        if (message.type === "dl-progress" && message.stage === "needs_user") {
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
// Fetch proxy: allowlisted F95 URLs, credentialed GET, 25-second abort,
// 4 MiB streamed cap, off-site redirect guard. Tabs are only queried for an
// open F95 tab to repeat a guest fetch in; no windows, offscreen, or cookies
// API use.
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

// Firefox sends no F95 cookies with background-page fetches, so F95 answers
// as a guest and Cloudflare challenges them without the browser's clearance.
// The same request from inside an open F95 tab is first-party and carries the
// browser's login and clearance. Once a background fetch came back as a guest
// or challenge page, later fetches use a tab first while one is open.
const F95_GUEST_PAGE_RE = /\bdata-logged-in=["']false["']/;
// The managed challenge loads /h/ scripts; signed-out pages only carry the
// passive /scripts/jsd/ probe.
const F95_CHALLENGE_PAGE_RE = /<title>\s*Just a moment|\/cdn-cgi\/challenge-platform\/h\//i;
let bridgePreferTabFetch = false;

function bridgeNeedsBrowserSession(body) {
    if (typeof body !== "string") {
        return false;
    }
    return F95_GUEST_PAGE_RE.test(body.slice(0, 4096)) || F95_CHALLENGE_PAGE_RE.test(body.slice(0, 65536));
}

// Runs inside the F95 tab as a content script. It must stay self-contained.
async function bridgeTabFetch(url, maxBytes, timeoutMs) {
    // Firefox's content.fetch runs as the page; Chrome content scripts already do.
    const hasPageFetch = typeof content !== "undefined" && content && typeof content.fetch === "function";
    const pageFetch = async (target, init) => {
        if (hasPageFetch) {
            try {
                return await content.fetch(target, init);
            } catch (_pageError) {
                // Fall back to the content script's own fetch.
            }
        }
        return fetch(target, init);
    };
    let timer = null;
    const timeout = new Promise((resolve) => {
        timer = setTimeout(() => resolve({ ok: false, error: "timeout" }), timeoutMs);
    });
    const request = (async () => {
        try {
            const response = await pageFetch(url, {
                method: "GET",
                credentials: "include",
                redirect: "follow",
                cache: "no-store",
            });
            const finalUrl = response.url || url;
            if (new URL(finalUrl).hostname !== "f95zone.to") {
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

// Returns null when no F95 tab can run the fetch.
async function bridgeFetchViaTab(url, timeoutMs = BRIDGE_FETCH_TIMEOUT_MS) {
    if (!isBridgeUrlAllowed(url)) {
        return { ok: false, error: "refused" };
    }
    try {
        if (typeof chrome === "undefined" || !chrome.tabs || !chrome.scripting) {
            return null;
        }
        const tabs = await chrome.tabs.query({ url: "https://f95zone.to/*" });
        const tab = (tabs || []).find((item) => item && typeof item.id === "number" && !item.discarded);
        if (!tab) {
            return null;
        }
        const injected = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: bridgeTabFetch,
            args: [url, BRIDGE_MAX_BODY_BYTES, timeoutMs],
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

// All attempts share one BRIDGE_FETCH_TIMEOUT_MS budget, so the answer
// arrives before the app stops waiting and starts its next F95 request.
async function bridgeFetchPreferringLogin(url) {
    const deadline = Date.now() + BRIDGE_FETCH_TIMEOUT_MS;
    const remaining = () => deadline - Date.now();
    if (bridgePreferTabFetch) {
        const viaTab = await bridgeFetchViaTab(url, remaining());
        if (viaTab) {
            return viaTab;
        }
        if (remaining() < BRIDGE_MIN_ATTEMPT_MS) {
            return { ok: false, error: "timeout" };
        }
    }
    const fetched = await bridgeFetchUrl(url, remaining());
    if (bridgePreferTabFetch || !fetched.ok || !bridgeNeedsBrowserSession(fetched.body)) {
        return fetched;
    }
    if (remaining() < BRIDGE_MIN_ATTEMPT_MS) {
        return fetched;
    }
    const viaTab = await bridgeFetchViaTab(url, remaining());
    if (!viaTab || !viaTab.ok) {
        return fetched;
    }
    bridgePreferTabFetch = true;
    return viaTab;
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
            return String(chrome.runtime.getManifest().version || "1.2.5");
        }
    } catch (error) {
        console.warn("wLib extension: version read failed.", error);
    }
    return "1.2.5";
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

async function bridgeSendHello() {
    if (!bridgeSocket || bridgeSocket.readyState !== 1) {
        return;
    }
    try {
        const canFetch = await bridgeGetCanFetch();
        bridgeSocket.send(JSON.stringify({
            type: "hello",
            protocol: 2,
            can_resolve: true,
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
        const fetched = await bridgeFetchPreferringLogin(url);
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
    for (let i = 0; i < buzzOrigins.length; i++) {
        const id = `wlib-buzz-resolve-${i}`;
        const origin = buzzOrigins[i];
        const allowed = await siteApi.permissions.contains({origins:[origin]});
        const exists = registered.some(script=>script.id === id);
        if (allowed && !exists) await siteApi.scripting.registerContentScripts([{id,matches:[origin],js:['bzzhr-resolve.js'],runAt:'document_start'}]);
        if (!allowed && exists) await siteApi.scripting.unregisterContentScripts({ids:[id]});
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
function closeTab(tabId) {
    try { const p = chrome.tabs?.remove(tabId); p?.catch?.(()=>{}); } catch (_) {}
}
function resolveEntryFor(sender, url) {
    const tabId = sender?.tab?.id;
    if (!Number.isInteger(tabId)) return null;
    const known = resolveWindows.get(tabId);
    if (known) {
        known.bound = true;
        return known;
    }
    if (typeof url !== 'string') return null;
    for (const [finishedUrl, expires] of resolveFinishedUrls) {
        if (expires < Date.now()) resolveFinishedUrls.delete(finishedUrl);
        else if (sameUrl(finishedUrl, url)) { closeTab(tabId); return null; }
    }
    for (const entry of new Set(resolveWindows.values())) {
        if (entry.finished || !sameUrl(entry.url, url)) continue;
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
    sendResolveMessage(answer);
    // A late copy of this URL (Zen and Firefox can load it twice) is closed
    // on sight, but only briefly and only after a success: after a failure
    // the user's own "Open in browser" loads the same URL and must stay.
    if (answer.ok) resolveFinishedUrls.set(entry.url, Date.now() + 10000);
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
async function bridgeHandleResolve(message) {
    const id = typeof message.id === 'string' ? message.id : '';
    const url = typeof message.url === 'string' ? message.url : '';
    if (!id || !/^https:\/\/f95zone\.to\/dl\/\d+\/\?[^#]+/.test(url)) {
        if (id) sendResolveMessage({type:'resolve_result',id,ok:false,error:'invalid download URL'});
        return;
    }
    let win;
    const socket = bridgeSocket;
    const opening = new AbortController();
    resolveOpening.set(id, opening);
    try {
        await resolveRecovery;
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
        const entry = {id,url,host:message.host,buzzAllowed,tabId,windowId:win.id,tabWindowId:win.id,bound:false,duplicates:new Set(),finished:false,needsUser:false};
        resolveWindows.set(tabId,entry);
        entry.timer = setTimeout(()=>finishResolve(entry,{ok:false,error:'timed out'}),30000);
        saveResolveWindows();
        sendResolveMessage({type:'resolve_progress',id,stage:'opened'});
    } catch (_) {
        if (win?.id != null) { try { await chrome.windows.remove(win.id); } catch (_) {} }
        sendResolveMessage({type:'resolve_result',id,ok:false,error:'could not open resolve window'});
    } finally {
        resolveOpening.delete(id);
    }
}
chrome.windows?.onRemoved?.addListener(windowId => {
    for (const entry of [...resolveWindows.values()]) if ((entry.tabWindowId ?? entry.windowId) === windowId) finishResolve(entry,{ok:false,error:'cancelled'});
});
chrome.tabs?.onRemoved?.addListener(tabId => {
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
