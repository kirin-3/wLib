// SPDX-License-Identifier: GPL-3.0-or-later
async page => {
  const check = (ok, message) => { if (!ok) throw Error(message); };
  await page.evaluate(() => {
    localStorage.setItem('wlib-mock-games', JSON.stringify([{id:1,title:'Filter fixture',exe_path:'/game',engine:"Ren'Py",play_status:'Playing'}]));
    localStorage.setItem('wlib-library-view-state',JSON.stringify({layoutMode:'grid',sortBy:'rating',filterStatuses:['Playing'],filterEngines:["Ren'Py"],isFiltersCollapsed:true}));
  });
  await page.reload();
  const status = page.getByRole('button',{name:'Remove filter Status: Playing'});
  const engine = page.getByRole('button',{name:"Remove filter Engine: Ren'Py"});
  await status.waitFor(); await engine.waitFor();
  check(await page.getByRole('button',{name:'Grid view'}).getAttribute('aria-pressed') === 'true','Grid selected');
  for (const name of ['List view','Compact view','Banner view']) check(await page.getByRole('button',{name}).getAttribute('aria-pressed') === 'false',`${name} unselected`);
  await page.getByRole('button',{name:'F95 rating'}).waitFor(); await page.getByRole('button',{name:'My rating'}).waitFor();
  await status.click(); check(await engine.isVisible() && await status.count() === 0,'Removing one preserves the other');
  await page.waitForTimeout(500); await page.reload(); await engine.waitFor(); check(await status.count() === 0,'Removal persists');
  await page.getByRole('button',{name:'Clear all',exact:true}).click(); check(await engine.count() === 0,'Clear all removes chips');
  return 'PASS: filter chips, persistence, sort and layout labels';
}
