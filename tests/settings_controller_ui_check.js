// SPDX-License-Identifier: GPL-3.0-or-later
async page => {
  const url=page.url(), viewport=page.viewportSize();
  const old=await page.evaluate(() => [localStorage.getItem('wlib-mock-platform'),localStorage.getItem('wlib-mock-settings')]);
  try {
    await page.setViewportSize({width:1440,height:800});
    await page.evaluate(() => { localStorage.setItem('wlib-mock-platform','linux'); localStorage.setItem('wlib-mock-settings',JSON.stringify({})); delete window.pywebview; });
    await page.goto(new URL('/#/settings',url).toString()); await page.reload();
    await page.locator('#settings-proton-path').waitFor();
    const dispatch = async action => page.evaluate(async action => {
      const url = performance.getEntriesByType('resource').map(x => x.name).find(x => /\/src\/utils\/controllerActions\.ts/.test(x));
      (await import(url)).dispatchController(action, () => {});
    },action);
    await page.locator('#general input[type="checkbox"]').first().focus();
    await dispatch({type:'move',direction:'left'});
    if (!await page.evaluate(() => !!document.activeElement?.closest('nav[aria-label="Settings sections"]'))) throw Error('Left did not enter section list');
    await page.getByRole('button',{name:'Appearance',exact:true}).focus();
    await dispatch({type:'activate'});
    if (!await page.evaluate(() => !!document.activeElement?.closest('#appearance'))) throw Error('A did not focus section control');
    await dispatch({type:'search'});
    if (!await page.locator('#settings-search-desktop').evaluate(el => document.activeElement === el)) throw Error('Y did not focus search');
    await page.getByRole('button',{name:'Appearance',exact:true}).focus();
    await dispatch({type:'activate'}); await dispatch({type:'nextTab'});
    if (!await page.evaluate(() => !!document.activeElement?.closest('#wine-proton'))) throw Error('RB did not focus next section');
    const proton = page.locator('#settings-proton-path');
    await proton.fill('/keyboard-save');
    await page.keyboard.press('ArrowDown');
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('wlib-mock-settings') || '{}').proton_path === '/keyboard-save');
    return 'PASS: left, A, Y, RB and keyboard focus movement saves';
  } finally {
    await page.evaluate(old => { delete window.pywebview; for (const [i,k] of ['wlib-mock-platform','wlib-mock-settings'].entries()) old[i] === null ? localStorage.removeItem(k) : localStorage.setItem(k,old[i]); },old);
    if(viewport) await page.setViewportSize(viewport); await page.goto(url);
  }
}
