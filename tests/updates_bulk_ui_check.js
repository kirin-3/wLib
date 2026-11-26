// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/updates_bulk_ui_check.js.
async page => {
  const original = await page.evaluate(() => localStorage.getItem('wlib-mock-games'));
  const url = page.url();
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem('wlib-mock-games', JSON.stringify(Array.from({length: 5}, (_, i) => ({id: i + 1, title: `Update ${i + 1}`, exe_path: '/game', version: '1', latest_version: '2', f95_url: `https://f95zone.to/threads/game.${i + 1}/`}))));
    });
    await page.goto(new URL('/#/updates', url).toString());
    await page.reload();
    const all = page.getByRole('checkbox', {name: /Select all/});
    await all.check();
    if (!(await page.getByText('Select all (5 selected)').count())) throw Error('select all count');
    // Some rows selected: Select all is partly selected (indeterminate), not checked.
    await page.getByRole('checkbox', {name: 'Select Update 1'}).uncheck();
    if (!(await all.evaluate(box => box.indeterminate && !box.checked))) throw Error('select all is not indeterminate with 4 of 5 selected');
    await all.check();
    if (await all.evaluate(box => box.indeterminate)) throw Error('select all stays indeterminate after selecting all');
    await page.getByRole('button', {name:'Mark updated', exact:true}).first().click();
    await page.getByRole('button', {name:'Undo', exact:true}).click();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('wlib-mock-games')).every(game => game.version === '1'));
    const row = page.locator('.section-card').filter({has: page.getByRole('heading', {name: /^Updates available/})});
    await row.getByRole('button', {name:'More update actions', exact:true}).last().click();
    await page.getByRole('menuitem', {name:'Skip version 2', exact:true}).click();
    await page.getByRole('button', {name:'Undo', exact:true}).last().click();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('wlib-mock-games')).every(game => !game.skipped_version));
    await all.check();
    await page.getByRole('button', {name:'Re-check', exact:true}).first().click();
    await page.getByText(/0\s*\/\s*5/).first().waitFor();
    return 'PASS: selected updates can be marked, undone and rechecked';
  } finally {
    await page.evaluate(value => value === null ? localStorage.removeItem('wlib-mock-games') : localStorage.setItem('wlib-mock-games', value), original);
    await page.goto(url);
  }
}
