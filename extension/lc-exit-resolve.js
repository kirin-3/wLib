// SPDX-License-Identifier: GPL-3.0-or-later
(async () => {
    const send = message => chrome.runtime.sendMessage(message);
    let owned = false;
    // The background records the tab after the browser creates the window.
    for (let attempt = 0; attempt < 30; attempt++) {
        try {
            const owner = await send({type:'dl-owned?',url:location.href});
            owned = owner?.owned && owner.site === 'lc';
        } catch (_) { return; }
        if (owned) break;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!owned) return;

    const report = message => { void send(message).catch(() => {}); };
    let reportedUser = false;
    let submitted = false;
    function check() {
        const exit = document.querySelector('[data-lc-exit]');
        if (!exit) return;
        if (exit.dataset.captchaRequired === '1') {
            if (!reportedUser) {
                reportedUser = true;
                report({type:'dl-progress',stage:'needs_user'});
            }
            const response = exit.querySelector('textarea[name="h-captcha-response"]');
            if (!response?.value) submitted = false;
            const button = exit.querySelector('form[action="/masked/continue"] button[type="submit"]');
            if (!submitted && response?.value && button && !button.disabled && document.readyState === 'complete') {
                submitted = true;
                button.click();
            }
            return;
        }
        const link = exit.querySelector('[data-exit-continue][href]');
        if (link?.href) {
            stop();
            report({type:'lc-exit-continue',url:link.href});
        }
    }
    // A user click can submit before the value poll runs. Let the page process it.
    document.addEventListener('submit', event => {
        if (event.target.matches('[data-lc-exit] form[action="/masked/continue"]')
            && event.target.querySelector('textarea[name="h-captcha-response"]')?.value) submitted = true;
    }, true);
    const observer = new MutationObserver(check);
    observer.observe(document, {subtree:true,childList:true,attributes:true});
    // The verification field's value can change without a DOM mutation.
    const poll = setInterval(check, 250);
    const fallback = setTimeout(() => {
        if (!reportedUser && !document.querySelector('[data-lc-exit]')) {
            reportedUser = true;
            report({type:'dl-progress',stage:'needs_user'});
        }
    }, 10000);
    function stop() {
        observer.disconnect();
        clearInterval(poll);
        clearTimeout(fallback);
    }
    check();
})();
