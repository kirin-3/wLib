// SPDX-License-Identifier: GPL-3.0-or-later
// Run against an isolated Vite mock session with playwright-cli run-code.
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const keys = ['wlib-mock-games','wlib-mock-settings','wlib-library-view-state','wlib-theme'];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.evaluate(() => {
      const link = (source) => ({ source, id: '1', url: 'https://example.com/' });
      localStorage.setItem('wlib-mock-games', JSON.stringify([
        { id: 1, title: 'F95 only', exe_path: '/a', f95_url: 'https://f95zone.to/threads/a.1/' },
        { id: 2, title: 'F95 DLsite', exe_path: '/b', f95_url: 'https://f95zone.to/threads/b.2/', links: [link('dlsite')] },
        { id: 3, title: 'Ryuugames linked', exe_path: '/c', links: [link('ryuugames'), link('dlsite'), link('vndb')] },
      ]));
      localStorage.setItem('wlib-library-view-state', JSON.stringify({layoutMode:'list', isFiltersCollapsed:false}));
      localStorage.setItem('wlib-mock-settings', JSON.stringify({library_filter_presets:[{name:'Old'}]}));
    });
    await page.reload();
    // A click on a source pill toggles include. Its "Exclude <name>" button toggles exclude.
    // The pill's state is on the wrapper around both buttons; an excluded name adds ", excluded".
    const source = name => page.getByRole('button', {name: new RegExp(`^${name}(?:\\s*,\\s*(?:included|excluded))?$`)});
    const sourceState = name => source(name).locator('xpath=..').getAttribute('data-filter-state');
    const excludeSource = async name => { await source(name).hover(); await page.getByRole('button', {name: `Exclude ${name}`, exact: true}).click(); };
    const title = name => page.getByRole('button', {name, exact:true});
    await title('F95 only').waitFor();
    for (const [name, expected] of [['F95Zone',['F95 only','F95 DLsite']], ['DLsite',['F95 DLsite','Ryuugames linked']], ['VNDB',['Ryuugames linked']], ['Ryuugames',['Ryuugames linked']]]) {
      await source(name).click();
      check(await sourceState(name) === 'include', `${name}: included`);
      for (const game of ['F95 only','F95 DLsite','Ryuugames linked']) check(await title(game).count() === (expected.includes(game) ? 1 : 0), `${name}: ${game}`);
      await excludeSource(name);
      check(await sourceState(name) === 'exclude', `${name}: excluded`);
      for (const game of ['F95 only','F95 DLsite','Ryuugames linked']) check(await title(game).count() === (expected.includes(game) ? 0 : 1), `${name} excluded: ${game}`);
      await excludeSource(name);
      check(await sourceState(name) === 'neutral', `${name}: neutral`);
    }
    await source('DLsite').click();
    await source('VNDB').click();
    // The Filters badge counts one per chip, the same as the chip row.
    check(await page.getByRole('button', {name:/^Filters/}).getAttribute('aria-label') === 'Filters, 2 active', 'each included source should count as one filter');
    await page.getByRole('button', {name:'Save as preset', exact:true}).click();
    await page.getByLabel('Saved filter name').fill('Sources test');
    await page.getByRole('button', {name:'Save', exact:true}).click();
    await page.reload();
    await source('F95Zone').click();
    await page.getByTitle("Apply the saved filter 'Sources test'").click();
    check(await source('DLsite').getAttribute('aria-pressed') === 'true' && await source('VNDB').getAttribute('aria-pressed') === 'true' && await source('F95Zone').getAttribute('aria-pressed') === 'false', 'new preset restores sources');
    await page.getByTitle("Apply the saved filter 'Old'").click();
    check(await source('DLsite').getAttribute('aria-pressed') === 'false' && await source('VNDB').getAttribute('aria-pressed') === 'false', 'old preset clears sources');
    // The footer theme control cycles System, Light, Dark; its name states the current mode.
    const themeToggle = page.getByRole('button', {name: /^Theme: /});
    for (const theme of ['light', 'dark']) {
      const wanted = `Theme: ${theme === 'dark' ? 'Dark' : 'Light'}.`;
      for (let step = 0; step < 3 && !(await themeToggle.getAttribute('aria-label')).startsWith(wanted); step++) await themeToggle.click();
      check((await themeToggle.getAttribute('aria-label')).startsWith(wanted), `${theme} theme selected`);
      check(await source('DLsite').isVisible(), `${theme} source visible`);
      await page.screenshot({path:`/tmp/wlib-source-${theme}.png`});
    }
    return 'All source filters, group count, old/new presets and both themes passed.';
  } finally {
    await page.evaluate(items => { for (const [key, value] of items) value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); }, previous);
    await page.reload();
  }
}
