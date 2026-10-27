// SPDX-License-Identifier: GPL-3.0-or-later
(() => {
    const page = WLibStoreLinks.parseStoreLink(location.href);
    if (page?.source !== 'vndb') return;
    const links = [...document.querySelectorAll('table.releases a[href], #releases a[href], #rel a[href]')]
        .map(anchor => WLibStoreLinks.parseStoreLink(anchor.href)).filter(link => link && ['dlsite','steam'].includes(link.source));
    WLibSiteWidget.show(page,links);
})();
