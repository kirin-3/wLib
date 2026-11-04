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
    await page.waitForFunction(() => document.querySelectorAll('nav[aria-label="Settings sections"] button').length >= 10);
    const sections = await page.locator('nav[aria-label="Settings sections"] button').evaluateAll(buttons => buttons.map(button => {
      const id = button.dataset.sectionId, section = document.getElementById(id);
      return [id, !!section, section?.querySelectorAll('[data-settings-row]').length || 0];
    }));
    if (sections.some(([, exists, rows]) => !exists || !rows)) throw Error(`Missing section or row: ${JSON.stringify(sections)}`);
    await page.getByRole('button',{name:'Appearance',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Settings sections"] [aria-current="location"]')?.textContent === 'Appearance');
    await page.goto(new URL('/#/settings#sandbox',url).toString());
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Settings sections"] [aria-current="location"]')?.textContent === 'Sandbox');
    for (const id of ['custom-tools','umu']) {
      await page.goto(new URL('/#/settings#' + id,url).toString());
      await page.waitForFunction(id => document.querySelector(`nav [data-section-id="${id}"]`)?.getAttribute('aria-current') === 'location', id);
    }
    await page.locator('#appearance').evaluate(el => el.scrollIntoView({block:'start'}));
    await page.waitForFunction(() => document.querySelector('nav [data-section-id="appearance"]')?.getAttribute('aria-current') === 'location');
    const search = page.locator('#settings-search-desktop');
    await search.fill('prefix');
    await page.locator('#settings-prefix-path').waitFor({state:'visible'});
    if (await page.locator('#general').isVisible()) throw Error('Unmatched General remains visible');
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
    await page.setViewportSize({width: 700,height:800});
    await page.locator('#settings-section-select').selectOption('appearance');
    await page.waitForFunction(() => document.querySelector('#settings-section-select')?.value === 'appearance');
    return 'PASS: section jumps, deep links, search, row coverage and hidden in-flight save';
  } finally {
    await page.evaluate(old => { delete window.pywebview; for (const [i,k] of ['wlib-mock-platform','wlib-mock-settings'].entries()) old[i] === null ? localStorage.removeItem(k) : localStorage.setItem(k,old[i]); },old);
    if (viewport) await page.setViewportSize(viewport); await page.goto(url);
  }
}
