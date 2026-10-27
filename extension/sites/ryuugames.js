// SPDX-License-Identifier: GPL-3.0-or-later
(() => {
    if (!document.body.classList.contains('single-post')) return;
    const article = document.querySelector('article[id^="post-"]');
    const id = article?.id?.match(/^post-(\d+)$/)?.[1] || document.body.className.match(/\bpostid-(\d+)\b/)?.[1];
    const page = id ? WLibStoreLinks.linkFromId('ryuugames',id) : WLibStoreLinks.parseStoreLink(document.querySelector('link[rel="shortlink"]')?.href || '');
    if (!page) return;
    const post = article?.querySelector('.entry-content') || document.querySelector('.entry-content');
    const links = [...(post?.querySelectorAll('a[href]') || [])].map(anchor => WLibStoreLinks.parseStoreLink(anchor.href)).filter(link => link && ['dlsite','steam','vndb'].includes(link.source));
    const title = document.querySelector('h1')?.textContent || '';
    for (const code of title.match(/\b(?:RJ|VJ|BJ)\d+\b/gi) || []) links.push(WLibStoreLinks.linkFromId('dlsite',code));
    // WordPress posts also print store URLs as plain text.
    for (const url of (post?.textContent || '').match(/https?:\/\/[^\s<>"']+/g) || []) {
        const link = WLibStoreLinks.parseStoreLink(url);
        if (link && ['dlsite','steam','vndb'].includes(link.source)) links.push(link);
    }
    WLibSiteWidget.show(page,links,{
        developer:(post?.textContent || '').match(/Developer\s*:\s*([^\n]+?)(?=Released?\s*date|\n|$)/i)?.[1]?.trim() || '',
        version:title.match(/\bv(?:er(?:sion)?)?\.?\s*(\d[\w.]*)/i)?.[1] || '',
    });
})();
