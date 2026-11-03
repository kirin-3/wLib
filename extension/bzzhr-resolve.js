// SPDX-License-Identifier: GPL-3.0-or-later
(async () => {
    const send = message => chrome.runtime.sendMessage(message);
    let ownership;
    try { ownership = await send({type:'dl-owned?'}); } catch (_) { return; }
    if (!ownership?.owned) return;
    let started = false;
    const check = async () => {
        if (started) return;
        const button = document.querySelector('.download-btn[hx-get]');
        if (!button) return;
        started = true;
        try {
            const route = new URL(button.getAttribute('hx-get'), location.href);
            if (route.origin !== location.origin) throw new Error('invalid download route');
            const response = await fetch(route.href, {headers:{'HX-Request':'true','HX-Current-URL':location.href},redirect:'manual'});
            const redirect = response.headers.get('HX-Redirect');
            if (!redirect) throw new Error('missing download URL');
            const url = new URL(redirect, location.href);
            if (!['http:','https:'].includes(url.protocol)) throw new Error('invalid download URL');
            await send({type:'dl-result',ok:true,url:url.href,host:'buzzheavier',share_url:location.href});
        } catch (_) {
            await send({type:'dl-result',ok:false,error:'Buzzheavier could not resolve the download'});
        }
    };
    new MutationObserver(check).observe(document,{subtree:true,childList:true,attributes:true});
    void check();
})();
