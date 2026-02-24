chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
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
