// SPDX-License-Identifier: GPL-3.0-or-later
// Only tabs claimed by the background resolver may interact with this page.
(async () => {
    const send = message => chrome.runtime.sendMessage(message);
    let ownership;
    // Window creation and content-script startup can race. The URL lets the
    // background claim this tab when the browser loaded it somewhere unexpected.
    for (let attempt = 0; attempt < 30; attempt++) {
        try { ownership = await send({type:'dl-owned?',url:location.href}); } catch (_) { return; }
        if (ownership?.owned) break;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!ownership?.owned) return;
    let clicked = false, reported = false, needsUser = false;
    const reportUser = () => {
        if (needsUser || reported) return;
        needsUser = true;
        void send({type:'dl-progress',stage:'needs_user'}).catch(()=>{});
    };
    const validMirror = value => {
        try {
            const url = new URL(value, location.href);
            return ['http:','https:'].includes(url.protocol) && url.hostname !== 'f95zone.to' && !url.hostname.endsWith('.f95zone.to') ? url.href : '';
        } catch (_) { return ''; }
    };
    const check = () => {
        if (reported) return;
        const handoff = document.querySelector('[data-xf-init~="f95dl-go"][data-url]');
        if (handoff) {
            reported = true;
            const url = validMirror(handoff.getAttribute('data-url'));
            if (!url) { void send({type:'dl-result',ok:false,error:'invalid mirror URL'}).catch(()=>{}); return; }
            const hostname = new URL(url).hostname;
            if (['bzzhr.to','www.bzzhr.to','buzzheavier.com','www.buzzheavier.com'].includes(hostname)) {
                if (!ownership.buzzAllowed?.includes(`${new URL(url).origin}/*`)) void send({type:'dl-result',ok:true,url,host:'buzzheavier',needs_permission:'buzzheavier'}).catch(()=>{});
            } else {
                void send({type:'dl-result',ok:true,url,host:ownership.host}).catch(()=>{});
            }
            return;
        }
        if (document.querySelector('iframe[src*="captcha"], .g-recaptcha, .h-captcha, .cf-turnstile, #challenge-form, form[action*="/login"]')) reportUser();
        const button = document.querySelector('.js-f95dlContinue');
        if (button && !button.disabled && !clicked && pageReady()) {
            clicked = true;
            // F95 answering Continue with the same page again means its check failed:
            // after two presses, hand the page to the user instead of looping.
            let presses = 0;
            try { presses = Number(sessionStorage.getItem('wlibDlPresses')) || 0; sessionStorage.setItem('wlibDlPresses', String(presses + 1)); } catch (_) {}
            if (presses >= 2) reportUser(); else button.click();
        }
    };
    // Press Continue only once F95's own script has run its check: the page
    // has loaded and, when it carries a check code, the code is in the form.
    // Pressing earlier submits an empty check and F95 serves the page again.
    function pageReady() {
        if (document.readyState !== 'complete') return false;
        const page = document.querySelector('[data-xf-init~="f95dl-page"]');
        const code = document.querySelector('.js-f95dlCode');
        if (page?.getAttribute('data-code') && code && !code.value) return false;
        return true;
    }
    const observer = new MutationObserver(check);
    observer.observe(document, {subtree:true,childList:true,attributes:true});
    // The check code arrives as an input value, which no observer reports.
    const poll = setInterval(() => { if (reported || clicked) clearInterval(poll); else check(); }, 250);
    check();
    setTimeout(() => { if (!clicked && !reported) reportUser(); },10000);
})();
