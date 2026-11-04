// SPDX-License-Identifier: GPL-3.0-or-later
// Run in a fresh playwright-cli session against the Vite mock app.
async page => {
  const check = (ok, message) => { if (!ok) throw Error(message); };
  await page.evaluate(() => {
    localStorage.setItem('wlib-mock-games', JSON.stringify([{id:1,title:'Action fixture',exe_path:'/game'}]));
    localStorage.removeItem('wlib-library-view-state');
  });
  await page.reload();
  await page.getByRole('checkbox', {name:'Action fixture. Press Enter for details.'}).press('Enter');
  const dialog = page.getByRole('dialog');
  const more = dialog.getByRole('button', {name:'More actions'});
  check(await dialog.locator('.footer-action').count() === 0, 'Footer must not contain Remove');
  for (const theme of ['light','dark']) {
    await page.evaluate(theme => { localStorage.setItem('wlib-theme',theme); document.documentElement.classList.toggle('light',theme === 'light'); }, theme);
    await dialog.screenshot({path:`/tmp/wlib-detail-${theme}.png`});
  }
  await more.click();
  await dialog.getByRole('menuitem', {name:'Remove from library'}).waitFor();
  await page.keyboard.press('Escape');
  check(await dialog.isVisible() && await more.getAttribute('aria-expanded') === 'false', 'Escape must close only the menu');
  await more.click();
  await page.evaluate(async () => {
    const url = performance.getEntriesByType('resource').map(x => x.name).find(x => /\/src\/utils\/controllerActions\.ts/.test(x));
    (await import(url)).dispatchController({type:'back'}, () => { window.__backCalled = true; });
  });
  check(await dialog.isVisible() && await more.getAttribute('aria-expanded') === 'false' && !await page.evaluate(() => window.__backCalled), 'Controller Back must close only the menu');
  await more.click(); await dialog.getByRole('menuitem', {name:'Remove from library'}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Undo',exact:true}).click();
  await page.getByRole('checkbox',{name:'Action fixture. Press Enter for details.'}).waitFor();
  return 'PASS: actions menu, Escape, Back, removal and Undo';
}
