// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/confirm_ui_check.js.
async page => {
  const url = page.url();
  const keys = ['wlib-mock-games','wlib-mock-settings','wlib-mock-attachments','wlib-library-view-state','wlib-mock-launch-targets'];
  const old = await page.evaluate(keys => keys.map(key => [key,localStorage.getItem(key)]),keys);
  const native = [];
  const listener = dialog => { native.push(dialog.message()); void dialog.dismiss(); };
  page.on('dialog',listener);
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem('wlib-mock-settings',JSON.stringify({collections:['Fixture collection']}));
      localStorage.setItem('wlib-library-view-state',JSON.stringify({layoutMode:'grid',isFiltersCollapsed:false}));
      localStorage.setItem('wlib-mock-games',JSON.stringify([{id:1,title:'Confirm fixture',exe_path:'/game',launch_targets:[{id:1,game_id:1,label:'Part two',exe_path:'/part',sort_order:0}]}]));
      localStorage.setItem('wlib-mock-attachments',JSON.stringify({'1':[{name:'guide.txt',size:42}]}));
    });
    await page.goto(new URL('/#/',url).toString()); await page.reload();
    const answer = async title => {
      const dialog = page.getByRole('dialog',{name:title,exact:true});
      await dialog.waitFor();
      if (!(await dialog.getByRole('button',{name:'Cancel'}).evaluate(el=>document.activeElement===el))) throw Error(`${title}: Cancel not focused`);
      await dialog.getByRole('button',{name:'Cancel'}).click();
    };
    const name = page.getByLabel('Saved filter name');
    await name.fill('Fixture filter'); await name.press('Enter');
    await name.fill('Fixture filter'); await name.press('Enter'); await answer('Replace saved filter');
    await page.getByRole('button',{name:'Delete the saved filter Fixture filter'}).click(); await answer('Delete saved filter');
    await page.getByRole('tab',{name:/Fixture collection/}).click({button:'right'});
    await page.getByRole('menuitem',{name:'Delete'}).click(); await answer('Delete collection');
    await page.locator('[data-game-id="1"]').click();
    const detail = page.getByRole('dialog').filter({has:page.getByRole('tablist',{name:'Game details'})});
    await detail.getByRole('tab',{name:'Launch',exact:true}).click();
    await detail.locator('.launch-target-row').filter({hasText:'Part two'}).getByRole('button',{name:'Remove'}).click(); await answer('Remove launch target');
    await detail.getByRole('tab',{name:'Notes',exact:true}).click();
    await detail.locator('li').filter({hasText:'guide.txt'}).getByRole('button',{name:'Remove'}).click(); await answer('Remove attachment');
    await detail.getByRole('tab',{name:'Info',exact:true}).click();
    await detail.locator('#detail-title').fill('Changed title');
    await detail.getByRole('button',{name:'Close',exact:true}).click(); await answer('Discard changes');
    if (native.length) throw Error(`Native dialogs: ${native.join(', ')}`);
    return 'PASS: saved filters, collection, launch target, attachment and discard dialogs';
  } finally {
    page.off('dialog',listener);
    await page.evaluate(items => { delete window.pywebview; for(const [key,value] of items) value===null ? localStorage.removeItem(key) : localStorage.setItem(key,value); },old);
    await page.goto(url);
  }
}
