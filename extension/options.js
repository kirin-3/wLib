// SPDX-License-Identifier: GPL-3.0-or-later
const origins = {dlsite:'https://www.dlsite.com/*',vndb:'https://vndb.org/*',ryuugames:'https://www.ryuugames.com/*',lewdcorner:'https://lewdcorner.com/*',buzzheavier:['https://bzzhr.to/*','https://www.bzzhr.to/*','https://buzzheavier.com/*','https://www.buzzheavier.com/*']};
const lcDownloads = document.getElementById('lc-downloads');
const lcAccess = {origins:[origins.lewdcorner]};
const webRequest = {permissions:['webRequest']};
let changingLcDownloads = false;
function syncLcDownloads() {
    chrome.permissions.contains(lcAccess, hasAccess => {
        chrome.permissions.contains(webRequest, granted => {
            lcDownloads.checked = hasAccess && granted;
            lcDownloads.disabled = changingLcDownloads || !hasAccess;
        });
    });
}
function changePermission(input, permission, done) {
    input.disabled = true;
    const complete = () => {
        const error = chrome.runtime.lastError;
        document.getElementById('status').textContent = error ? error.message : '';
        done();
    };
    if (input.checked) chrome.permissions.request(permission, complete);
    else chrome.permissions.remove(permission, complete);
}
for (const input of document.querySelectorAll('input[data-site]')) {
    const siteOrigins = origins[input.dataset.site];
    const permission = {origins:Array.isArray(siteOrigins) ? siteOrigins : [siteOrigins]};
    const sync = () => chrome.permissions.contains(permission, granted => { input.checked = granted; });
    sync();
    chrome.permissions.onAdded.addListener(sync);
    chrome.permissions.onRemoved.addListener(sync);
    input.addEventListener('change', () => changePermission(input, permission, () => {
        sync();
        input.disabled = false;
        syncLcDownloads();
    }));
}
lcDownloads.addEventListener('change', () => {
    changingLcDownloads = true;
    changePermission(lcDownloads, webRequest, () => {
        changingLcDownloads = false;
        syncLcDownloads();
    });
});
chrome.permissions.onAdded.addListener(syncLcDownloads);
chrome.permissions.onRemoved.addListener(syncLcDownloads);
syncLcDownloads();
chrome.runtime.sendMessage?.({action:'pairingCode'}, response => {
    document.getElementById('pairing-code').textContent = (!chrome.runtime.lastError && response?.code) || 'not available';
});
