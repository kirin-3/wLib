// SPDX-License-Identifier: GPL-3.0-or-later
const origins = {dlsite:'https://www.dlsite.com/*',vndb:'https://vndb.org/*',ryuugames:'https://www.ryuugames.com/*',buzzheavier:['https://bzzhr.to/*','https://www.bzzhr.to/*','https://buzzheavier.com/*','https://www.buzzheavier.com/*']};
for (const input of document.querySelectorAll('input[data-site]')) {
    const siteOrigins = origins[input.dataset.site];
    const permission = {origins:Array.isArray(siteOrigins) ? siteOrigins : [siteOrigins]};
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
chrome.runtime.sendMessage?.({action:'pairingCode'}, response => {
    document.getElementById('pairing-code').textContent = (!chrome.runtime.lastError && response?.code) || 'not available';
});
