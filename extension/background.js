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

function normalizeCheckPayload(data) {
    const payload = {
        exists: Boolean(data && data.exists),
    };

    if (payload.exists && data && typeof data.playStatus === 'string' && data.playStatus.trim()) {
        payload.playStatus = data.playStatus.trim();
    }

    return payload;
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

    if (message.action === "openWLib") {
        fetch(`http://localhost:8183/api/open?url=${encodeURIComponent(message.url)}`, { headers: WLIB_HEADERS })
            .then(readWLibResponse)
            .then(data => sendResponse({ success: true, data }))
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }
});
