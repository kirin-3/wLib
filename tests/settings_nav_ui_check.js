// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/settings_nav_ui_check.js.
async page => {
  const url = page.url(), viewport = page.viewportSize();
  const old = await page.evaluate(() => [localStorage.getItem('wlib-mock-platform'), localStorage.getItem('wlib-mock-settings')]);
  try {
    await page.setViewportSize({width: 1440, height: 800});
    await page.evaluate(() => { localStorage.setItem('wlib-mock-platform','linux'); localStorage.setItem('wlib-mock-settings',JSON.stringify({proton_path:'/old'})); delete window.pywebview; });
    await page.goto(new URL('/#/settings',url).toString()); await page.reload();
    await page.locator('#settings-proton-path').waitFor();
    await page.waitForFunction(() => document.querySelectorAll('nav[aria-label="Settings sections"] button').length === 7);
    const sections = await page.locator('nav[aria-label="Settings sections"] button').evaluateAll(buttons => buttons.map(button => {
      const id = button.dataset.sectionId, section = document.getElementById(id);
      return [id, !!section, section?.querySelectorAll('[data-settings-row]').length || 0];
    }));
    if (sections.some(([, exists, rows]) => !exists || !rows)) throw Error(`Missing section or row: ${JSON.stringify(sections)}`);
    await page.getByRole('button',{name:'Appearance',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Settings sections"] [aria-current="location"]')?.textContent === 'Appearance');
    // Old section ids still work: a group opens under its section's sticky heading, an old section opens its new section.
    const links = {sandbox:'running-games','custom-tools':'running-games',umu:'running-games','wine-proton':'running-games',tools:'running-games',renpy:'running-games',
      catalog:'sources','file-hosts':'downloads',torrents:'downloads','keyboard-shortcuts':'advanced',general:'library','library-downloads':'downloads',steam:'steam'};
    for (const [id, section] of Object.entries(links)) {
      await page.goto(new URL('/#/settings#' + id,url).toString());
      await page.waitForFunction(section => document.querySelector(`nav [data-section-id="${section}"]`)?.getAttribute('aria-current') === 'location', section);
      await page.waitForTimeout(100);
      const gap = await page.evaluate(([id, section]) => {
        const target = document.getElementById(section === id || ['general','library-downloads'].includes(id) ? section : id);
        const head = document.getElementById(section).firstElementChild;
        const scroller = document.querySelector('main'), main = scroller.getBoundingClientRect().top;
        // A target near the end of the page stops where the page cannot scroll further.
        if (scroller.scrollTop >= scroller.scrollHeight - scroller.clientHeight - 1) return 0;
        return target === document.getElementById(section) ? target.getBoundingClientRect().top - main : target.getBoundingClientRect().top - head.getBoundingClientRect().bottom;
      }, [id, section]);
      if (Math.abs(gap) > 2) throw Error(`#${id} opened ${gap}px away from the top`);
    }
    await page.locator('#appearance').evaluate(el => el.scrollIntoView({block:'start'}));
    await page.waitForFunction(() => document.querySelector('nav [data-section-id="appearance"]')?.getAttribute('aria-current') === 'location');
    const search = page.locator('#settings-search-desktop');
    await search.fill('prefix');
    await page.locator('#settings-prefix-path').waitFor({state:'visible'});
    if (await page.locator('#library').isVisible()) throw Error('Unmatched Library remains visible');
    if (await page.locator('#sandbox').isVisible()) throw Error('Unmatched Sandbox group remains visible');
    await search.fill('dark');
    await page.getByRole('group',{name:'Theme'}).waitFor({state:'visible'});
    await search.fill("ren'py");
    await page.locator('#renpy #urm-source').waitFor({state:'visible'});
    await search.fill('zzzz-no-setting');
    await page.getByText('No settings match.',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Clear search'}).click();
    await page.locator('#settings-proton-path').waitFor({state:'visible'});
    await page.evaluate(() => {
      window.__data = {...JSON.parse(localStorage.getItem('wlib-mock-settings') || '{}'),library_folders:[],library_folder:''};
      window.pywebview = {api:{save_settings: payload => new Promise(resolve => setTimeout(() => {Object.assign(window.__data,payload);resolve({success:true});},600)),get_settings: () => window.__data}};
    });
    const proton = page.locator('#settings-proton-path'); await proton.fill('/hidden-save'); await proton.blur();
    await page.getByText('Saving…',{exact:true}).first().waitFor();
    await search.fill('dark');
    await page.waitForFunction(() => window.__data.proton_path === '/hidden-save');
    await search.fill('');
    if (await proton.inputValue() !== '/hidden-save') throw Error('Hidden field lost its saved value');
    // The current section follows a jump even when the page cannot scroll that section to the top.
    const current = () => page.evaluate(() => document.querySelector('[data-settings-nav] [aria-current="location"]')?.dataset.sectionId);
    const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const scrollMain = top => page.evaluate(top => { const main = document.querySelector('main'); main.scrollTop = top === 'end' ? main.scrollHeight : top; }, top);
    const lastId = await page.locator('[data-settings-nav] button').last().getAttribute('data-section-id');
    await page.locator('[data-settings-nav] button').last().click();
    await settle(); await page.waitForTimeout(250); await settle();
    if (await current() !== lastId) throw Error(`Last item click left ${await current()} current`);
    if (await page.locator('[data-settings-nav] button').last().getAttribute('aria-current') !== 'location') throw Error('Last item has no aria-current');
    // Scrolling to the end marks the last section, also after the page was elsewhere.
    await scrollMain(0); await page.waitForFunction(() => document.querySelector('[data-settings-nav] [aria-current]')?.dataset.sectionId === 'library');
    await scrollMain('end'); await page.waitForFunction(last => document.querySelector('[data-settings-nav] [aria-current]')?.dataset.sectionId === last, lastId);
    // After a jump, a wheel scroll gives the current section back to the scroll position.
    await page.getByRole('button',{name:'Appearance',exact:true}).click(); await settle();
    if (await current() !== 'appearance') throw Error('Appearance jump not current');
    const box = await page.locator('[data-settings-content]').boundingBox();
    await page.mouse.move(box.x + box.width / 2, 400);
    await page.mouse.wheel(0, -1200); await page.waitForTimeout(300); await settle();
    const expected = await page.evaluate(() => {
      const top = document.querySelector('main').getBoundingClientRect().top + 20;
      const ids = [...document.querySelectorAll('[data-settings-nav] button')].map(b => b.dataset.sectionId);
      return ids.filter(id => document.getElementById(id).getBoundingClientRect().top <= top).pop() || ids[0];
    });
    if (expected === 'appearance' || await current() !== expected) throw Error(`Wheel after jump: current ${await current()}, expected ${expected}`);
    // "/" focuses settings search here (the library page uses it for library search).
    await page.locator('#settings-search-desktop').evaluate(el => el.blur());
    await page.keyboard.press('/');
    if (await page.evaluate(() => document.activeElement?.id) !== 'settings-search-desktop') throw Error('"/" did not focus settings search');
    await page.locator('#settings-search-desktop').evaluate(el => el.blur());
    await page.keyboard.press('Control+f');
    if (await page.evaluate(() => document.activeElement?.id) !== 'settings-search-desktop') throw Error('Ctrl+F did not focus settings search');
    await page.locator('#settings-search-desktop').evaluate(el => el.blur());
    // Narrow windows: search and the section menu stay at the top, and section headings stick under them.
    await page.setViewportSize({width: 900, height: 800}); await settle();
    const narrow = async () => page.evaluate(() => {
      const main = document.querySelector('main').getBoundingClientRect(), bar = document.getElementById('settings-search-mobile').closest('.settings-narrow-bar').getBoundingClientRect();
      const select = document.getElementById('settings-section-select');
      const head = document.getElementById(select.value).firstElementChild.getBoundingClientRect();
      return {barTop: bar.top - main.top, barBottom: bar.bottom, headTop: head.top, select: select.value, height: bar.height};
    });
    await scrollMain(Math.round(await page.evaluate(() => document.querySelector('main').scrollHeight * 0.6))); await page.waitForTimeout(150); await settle();
    let bar = await narrow();
    if (Math.abs(bar.barTop) > 1 || bar.height < 20) throw Error(`Narrow bar scrolled away: ${JSON.stringify(bar)}`);
    if (bar.headTop < bar.barBottom - 1 || bar.headTop > bar.barBottom + 1) throw Error(`Section heading not under the bar: ${JSON.stringify(bar)}`);
    await page.locator('#settings-section-select').selectOption('sources'); await settle();
    bar = await narrow();
    if (bar.select !== 'sources' || Math.abs(bar.headTop - bar.barBottom) > 1) throw Error(`Section menu jump hides the heading: ${JSON.stringify(bar)}`);
    await page.locator('#settings-search-mobile').evaluate(el => el.blur());
    await page.keyboard.press('/');
    if (await page.evaluate(() => document.activeElement?.id) !== 'settings-search-mobile') throw Error('"/" did not focus the narrow search');
    await page.setViewportSize({width: 700,height:800});
    await page.locator('#settings-section-select').selectOption('appearance');
    await page.waitForFunction(() => document.querySelector('#settings-section-select')?.value === 'appearance');
    return 'PASS: section jumps, current section, deep links, search, shortcut, narrow bar, row coverage and hidden in-flight save';
  } finally {
    await page.evaluate(old => { delete window.pywebview; for (const [i,k] of ['wlib-mock-platform','wlib-mock-settings'].entries()) old[i] === null ? localStorage.removeItem(k) : localStorage.setItem(k,old[i]); },old);
    if (viewport) await page.setViewportSize(viewport); await page.goto(url);
  }
}
