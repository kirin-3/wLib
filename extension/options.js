// SPDX-License-Identifier: GPL-3.0-or-later
const origins = {dlsite:'https://www.dlsite.com/*',vndb:'https://vndb.org/*',ryuugames:'https://www.ryuugames.com/*'};
for (const input of document.querySelectorAll('input[data-site]')) {
    const permission = {origins:[origins[input.dataset.site]]};
    chrome.permissions.contains(permission, granted => { input.checked = granted; });
    input.addEventListener('change', () => {
        input.disabled = true;
        const done = () => {
            const error = chrome.runtime.lastError;
            document.getElementById('status').textContent = error ? error.message : '';
            chrome.permissions.contains(permission, granted => { input.checked = granted; input.disabled = false; });
        };
        if (input.checked) chrome.permissions.request(permission, done);
        else chrome.permissions.remove(permission, done);
    });
}
