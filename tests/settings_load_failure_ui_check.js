// SPDX-License-Identifier: GPL-3.0-or-later
async page => {
  const url = page.url();
  try {
    await page.goto(new URL('/#/',url).toString());
    await page.evaluate(() => {
      window.__saves = 0; window.__available = false;
      window.pywebview = {api:{
        get_settings: () => window.__available ? {proton_path:'/stored', wine_prefix_path:'/prefix', library_folder:'', library_folders:[], custom_play_statuses:[], collections:[]} : Promise.reject(new Error('database locked')),
        save_settings: () => { window.__saves++; return {success:true}; },
      }};
    });
    await page.getByRole('link',{name:'Settings',exact:true}).click();
    await page.getByRole('alert').filter({hasText:'Could not load settings'}).waitFor();
    const folder = page.getByLabel('Downloads folder');
    if (!await folder.isDisabled()) throw Error('Field is enabled after settings load failed');
    if (!await page.getByLabel('Enable debug logging').isDisabled()) throw Error('Immediate toggle is enabled after settings load failed');
    await page.getByRole('link',{name:'Library',exact:true}).click();
    await page.waitForTimeout(200);
    if (await page.evaluate(() => window.__saves) !== 0) throw Error('Failed load caused a save');
    await page.evaluate(() => { window.__available = true; });
    await page.getByRole('link',{name:'Settings',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Downloads folder"]') && !document.querySelector('[aria-label="Downloads folder"]').disabled);
    await page.getByLabel('Downloads folder').fill('/new-downloads');
    await page.getByLabel('Downloads folder').blur();
    await page.waitForFunction(() => window.__saves === 1);
    return 'PASS: failed settings load blocks autosaves; a successful load enables them';
  } finally { await page.evaluate(() => { delete window.pywebview; }); await page.goto(url); await page.reload(); }
}
