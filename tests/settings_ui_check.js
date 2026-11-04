// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/settings_ui_check.js.
async page => {
  const url = page.url();
  const keys = ['wlib-mock-settings', 'wlib-mock-platform', 'wlib-mock-games'];
  const old = await page.evaluate(keys => keys.map(k => [k, localStorage.getItem(k)]), keys);
  let step = 'setup';
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem('wlib-mock-platform', 'linux');
      localStorage.setItem('wlib-mock-games', '[]');
      localStorage.setItem('wlib-mock-settings', JSON.stringify({ proton_path: '/old', wine_prefix_path: '/prefix', library_folders: ['/existing'], library_folder: '/existing' }));
    });
    await page.goto(new URL('/#/settings', url).toString()); await page.reload();
    const proton = page.locator('#settings-proton-path');
    const prefix = page.locator('#settings-prefix-path');
    await proton.waitFor(); await page.waitForFunction(() => !document.querySelector('#settings-proton-path').disabled);
    if (await page.getByRole('button', {name:'Save Changes'}).count() || await page.getByRole('button', {name:'Discard', exact:true}).count()) throw Error('Shared save actions remain');
    await page.evaluate(() => {
      window.__data = { ...JSON.parse(localStorage.getItem('wlib-mock-settings') || '{}'), library_folders: ['/existing'], library_folder: '/existing' };
      window.__calls = []; window.__fail = false;
      window.pywebview = {api:{
        save_settings: payload => new Promise(resolve => setTimeout(() => {
          window.__calls.push(payload);
          if (window.__fail) { window.__fail = false; resolve({success:false,error:'fixture failure'}); }
          else { Object.assign(window.__data, payload); resolve({success:true}); }
        }, 500)),
        get_settings: () => new Promise(resolve => setTimeout(() => resolve({...window.__data}), 300)),
      }};
    });
    step = 'first save';
    await proton.fill('/new-proton'); await proton.blur();
    await page.getByText('Saving…', {exact:true}).first().waitFor();
    await prefix.fill('/editing-prefix');
    await page.waitForFunction(() => window.__data.proton_path === '/new-proton');
    await page.getByText('Saved', {exact:true}).first().waitFor();
    if (await prefix.inputValue() !== '/editing-prefix') throw Error('Focused edit was overwritten by reload');
    step = 'prefix save';
    await prefix.blur();
    await page.waitForFunction(() => window.__data.wine_prefix_path === '/editing-prefix');
    step = 'failed save';
    await page.evaluate(() => { window.__fail = true; });
    await proton.fill('/retry'); await proton.blur();
    await page.getByRole('alert').filter({hasText:'fixture failure'}).waitFor();
    if (await proton.inputValue() !== '/retry') throw Error('Failed save lost typed value');
    await proton.focus(); await proton.blur();
    await page.waitForFunction(() => window.__data.proton_path === '/retry');
    step = 'status save';
    const status = page.getByLabel('Custom status name');
    await status.fill('Custom'); await page.getByRole('button',{name:'Add Status'}).click();
    await page.waitForFunction(() => window.__data.custom_play_statuses?.includes('Custom'));
    const count = await page.evaluate(() => window.__calls.length);
    await status.fill('Custom'); await page.getByRole('button',{name:'Add Status'}).click();
    await page.getByRole('alert').filter({hasText:/already|duplicate/i}).waitFor();
    if (await page.evaluate(() => window.__calls.length) !== count) throw Error('Duplicate status saved');
    step = 'URM and duplicate folder';
    const urm = page.locator('#urm-source');
    await urm.fill('/new-mod.rpa'); await urm.blur();
    await page.waitForFunction(() => window.__data.urm_rpa_path === '/new-mod.rpa');
    await page.evaluate(() => { window.pywebview.api.browse_directory = () => '/existing'; });
    await page.getByRole('button',{name:'Add library folder'}).click();
    await page.getByText('That folder is already a library folder.').waitFor();
    if (await page.getByLabel('/existing',{exact:true}).count() !== 1) throw Error('Duplicate folder was added');
    step = 'leave flush';
    await page.getByLabel('Library folder pattern').fill('{title}/new');
    await page.evaluate(() => { window.location.hash = '#/'; });
    await page.waitForFunction(() => window.__data.library_folder_pattern === '{title}/new');
    return 'PASS: autosave progress, failure retry, reload protection, duplicate status and leave flush';
  } catch (error) { throw new Error(`${step}: ${error}`);
  } finally {
    await page.evaluate(old => { delete window.pywebview; for (const [k,v] of old) v === null ? localStorage.removeItem(k) : localStorage.setItem(k,v); },old);
    await page.goto(url);
  }
}
