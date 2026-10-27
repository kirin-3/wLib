// SPDX-License-Identifier: GPL-3.0-or-later
(() => {
    const page = WLibStoreLinks.parseStoreLink(location.href);
    if (page?.source === 'dlsite') WLibSiteWidget.show(page,[],{developer:document.querySelector('.maker_name a, a[href*="/maker_id/"]')?.textContent?.trim() || ''});
})();
