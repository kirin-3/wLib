// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/controller_bulk_ui_check.js.
async page => {
  const old = await page.evaluate(() => localStorage.getItem('wlib-mock-games'));
  const url = page.url();
  try {
    await page.evaluate(() => localStorage.setItem('wlib-mock-games', JSON.stringify([1,2].map(id => ({id, title:`Controller ${id}`, exe_path:'/game'})))));
    await page.goto(new URL('/#/',url).toString()); await page.reload();
    const cards = page.locator('[data-game-id]'); await cards.first().waitFor();
    await page.evaluate(() => { window.__controllerPlay = 0; window.pywebview = {api:{launch_game: async () => { window.__controllerPlay++; return {success:true}; }}}; });
    const dispatch = async action => page.evaluate(async action => {
      const url = performance.getEntriesByType('resource').map(x => x.name).find(x => /\/src\/utils\/controllerActions\.ts/.test(x));
      const module = await import(url);
      module.dispatchController({type: action}, () => { window.__backCalled = true; });
    },action);
    await cards.first().focus(); await dispatch('menu');
    await page.getByRole('dialog',{name:/Actions for/}).getByRole('button',{name:'Select',exact:true}).click();
    await cards.nth(1).focus(); await dispatch('activate');
    const bar = page.getByRole('region',{name:'Bulk actions'}); await bar.getByText('2 selected').waitFor();
    await cards.nth(1).focus(); await dispatch('play');
    await page.waitForFunction(() => window.__controllerPlay === 1);
    await dispatch('menu');
    if (!(await bar.evaluate(el => el.contains(document.activeElement)))) throw Error('Menu did not focus bulk bar');
    await bar.getByRole('button',{name:/^Metadata/}).click();
    const addTag = bar.getByRole('menuitem',{name:/^Add tag/});
    await addTag.focus();
    if (!(await addTag.evaluate(el => document.activeElement === el))) throw Error('Metadata menu item did not take focus');
    await dispatch('back');
    if (await bar.getByRole('menu').count() || !(await bar.count())) throw Error('Back did not close the open bulk menu before clearing selection');
    await cards.first().focus(); await dispatch('back');
    if (await bar.count() || await page.evaluate(() => !!window.__backCalled)) throw Error('Back did not clear selection first');
    await page.evaluate(async () => {
      const resources = performance.getEntriesByType('resource').map(x => x.name);
      const modality = await import(resources.find(x => /\/src\/utils\/inputModality\.ts/.test(x)));
      const toast = await import(resources.find(x => /\/src\/utils\/toast\.ts/.test(x)));
      modality.setInputModality('controller');
      toast.notifyUndo('Controller undo fixture', async () => {});
    });
    const undo = page.getByRole('button',{name:'Undo',exact:true}).last();
    await undo.waitFor();
    if (!(await undo.evaluate(el => document.activeElement === el))) throw Error('Undo did not receive controller focus');
    await undo.click();
    return 'PASS: controller selection, play, menu and back';
  } finally {
    await page.evaluate(value => { delete window.pywebview; value === null ? localStorage.removeItem('wlib-mock-games') : localStorage.setItem('wlib-mock-games',value); },old);
    await page.goto(url);
  }
}
