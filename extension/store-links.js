// SPDX-License-Identifier: GPL-3.0-or-later
// Mirrored in core/store_links.py; both implementations use one fixture table.
(() => {
    function parseStoreLink(value) {
        if (typeof value !== 'string' || !/^https?:\/\//i.test(value.trim())) return null;
        let url;
        try { url = new URL(value.trim()); } catch { return null; }
        if (url.username || url.password) return null;
        const host = url.hostname.toLowerCase();
        const path = url.pathname || '/';
        const query = new URL(url.href).searchParams;
        url.hash = ''; url.search = '';
        let source = 'other', id = url.href, match;
        if (['dlsite.com', 'www.dlsite.com'].includes(host)) {
            match = path.match(/\/product_id\/((?:RJ|VJ|BJ)\d+)(?:\.html)?(?:\/|$)/i);
            if (!match) return null;
            source = 'dlsite'; id = match[1].toUpperCase();
        } else if (host === 'store.steampowered.com') {
            match = path.match(/^\/app\/(\d+)(?:\/|$)/);
            if (!match) return null;
            source = 'steam'; id = match[1];
        } else if (host.endsWith('.itch.io')) {
            const creator = host.slice(0, -'.itch.io'.length);
            match = path.match(/^\/([^/]+)(?:\/|$)/);
            if (!match || creator.includes('.')) return null;
            source = 'itch'; id = `${creator}/${match[1]}`;
        } else if (['dmm.co.jp','dmm.com','fanza.com'].some(domain => host === domain || host.endsWith('.' + domain))) {
            if (!/\/detail(?:\/|$)/i.test(path)) return null;
            match = (path + (query.size ? '?' + query.toString() : '')).match(/(?:^|[/?&])cid=([a-z_0-9]+)(?:\/|&|$)/i) || path.match(/\/detail\/([a-z_0-9]+)(?:\/|$)/i);
            if (!match) return null;
            source = 'fanza'; id = match[1].toLowerCase();
            if (query.has('cid')) url.search = `?cid=${id}`;
        } else if (['vndb.org','www.vndb.org'].includes(host)) {
            match = path.match(/^\/(v\d+)\/?$/i);
            if (!match) return null;
            source = 'vndb'; id = match[1].toLowerCase();
        } else if (['ryuugames.com','www.ryuugames.com'].includes(host)) {
            id = query.get('p') || '';
            if (!/^[1-9]\d*$/.test(id)) return null;
            return {source:'ryuugames',id,url:`https://www.ryuugames.com/?p=${id}`};
        } else if (['patreon.com','www.patreon.com','subscribestar.com','www.subscribestar.com','subscribestar.adult','www.subscribestar.adult','gumroad.com','www.gumroad.com'].includes(host)) {
            match = path.match(/^\/(?:c\/)?([^/]+)(?:\/|$)/);
            if (!match) return null;
            source = host.replace(/^www\./,'').split('.')[0]; id = match[1].toLowerCase();
        } else if (host.endsWith('.gumroad.com')) {
            source = 'gumroad'; id = host.slice(0,-'.gumroad.com'.length);
        } else if (['f95zone.to','www.f95zone.to'].includes(host)) return null;
        return {source,id,url:url.href};
    }
    function linkFromId(source, id) {
        if (source === 'itch' && /^[a-z0-9_-]+\/[^/?#]+$/.test(id)) {
            const [creator,game] = id.split('/');
            return parseStoreLink(`https://${creator}.itch.io/${game}`);
        }
        const rules = {
            fanza:[/^[a-z_0-9]+$/i,`https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=${id}/`],
            dlsite:[/^(RJ|VJ|BJ)\d+$/i,`https://www.dlsite.com/maniax/work/=/product_id/${id}.html`],
            steam:[/^\d+$/,`https://store.steampowered.com/app/${id}/`],
            vndb:[/^v\d+$/i,`https://vndb.org/${id}`],
            ryuugames:[/^[1-9]\d*$/,`https://www.ryuugames.com/?p=${id}`],
        };
        return rules[source]?.[0].test(id) ? parseStoreLink(rules[source][1]) : null;
    }
    function collectF95Links(wrapper) {
        if (!wrapper || wrapper.querySelector('.messageHide--link')) return null;
        const links = new Map();
        let text = '', labels = [], urls = [], bold = 0, label = '';
        const finish = () => {
            const match = text.match(/^\s*(Developer|Publisher|Stores?)\s*:/i);
            if (match && labels.includes(match[1].toLowerCase())) for (const url of urls) {
                const link = parseStoreLink(url);
                if (link) links.set(`${link.source}:${link.id}`,link);
            }
            text = ''; labels = []; urls = [];
        };
        const walk = node => {
            if (node.nodeType === 3) { text += node.textContent; if (bold) label += node.textContent; return; }
            if (node.nodeType !== 1) return;
            if (node.matches('.bbCodeSpoiler, .bbCodeBlock--spoiler')) { finish(); return; }
            const tag = node.tagName.toLowerCase();
            if (['br','div','p','hr'].includes(tag)) finish();
            if (['b','strong'].includes(tag)) { if (!bold) label = ''; bold++; }
            if (tag === 'a') urls.push(node.getAttribute('href') || '');
            for (const child of node.childNodes) walk(child);
            if (['b','strong'].includes(tag)) { bold--; if (!bold) labels.push(label.trim().replace(/:\s*$/,'').trim().toLowerCase()); }
            if (['div','p'].includes(tag)) finish();
        };
        for (const child of wrapper.childNodes) walk(child);
        finish();
        return [...links.values()];
    }
    globalThis.WLibStoreLinks = {parseStoreLink,linkFromId,collectF95Links};
})();
