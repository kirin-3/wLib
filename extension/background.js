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
                "Content-Type": "application/json"
            },
            body: JSON.stringify(message.payload)
        })
            .then(response => response.json())
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
        fetch(`http://localhost:8183/api/open?url=${encodeURIComponent(message.url)}`)
            .then(response => response.json())
            .then(data => sendResponse({ success: true, data }))
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }
});
