// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/updates_screen_reader_ui_check.js.
// Screen reader semantics of the Updates page and the build picker: one live
// region that speaks only when a check starts or ends (never the per-game
// "Checking: X" line), a named progressbar, focusable blocked buttons with a
// reason, the picker's mirrors and torrents as radio groups with arrow keys,
// and focus back on the row after the picker closes.
async page => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const previousUrl = page.url();
  const keys = ['wlib-mock-games', 'wlib-theme', 'wlib-ui-motion', 'wlib-mock-rpdl', 'wlib-mock-platform'];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const games = [
    {id: 81, title: 'Alpha game', exe_path: '/a', version: '1', latest_version: '2', f95_url: 'https://f95zone.to/threads/alpha.81/'},
    {id: 82, title: 'Beta game', exe_path: '/b', version: '1', latest_version: '2', f95_url: 'https://f95zone.to/threads/beta.82/'},
  ];
  const open = async () => {
    await page.evaluate(games => {
      delete window.pywebview;
      localStorage.setItem('wlib-ui-motion', 'off');
      localStorage.setItem('wlib-mock-platform', 'linux');
      localStorage.setItem('wlib-mock-games', JSON.stringify(games));
    }, games);
    await page.goto(new URL('/#/updates', previousUrl).toString());
    await page.reload();
    await page.getByRole('heading', {name: /^Updates available/}).waitFor();
  };
  const announcer = page.getByTestId('update-check-announcer');
  try {
    // A controlled check: the status says which game is being read.
    await open();
    await page.evaluate(() => {
      window.testStatus = {running: false, cancelling: false, outcome: 'idle', error: '', total: 0, checked: 0, current: '', results: [], delay_seconds: 2};
      window.pywebview = {api: {
        check_all_updates: async () => {
          window.testStatus = {...window.testStatus, running: true, outcome: 'running', total: 2, checked: 0, current: 'Alpha game'};
          return {success: true, total: 2, delay_seconds: 2};
        },
        get_update_status: async () => window.testStatus,
      }};
    });
    check(await announcer.count() === 1 && (await announcer.innerText()).trim() === '', 'announcer is in the page and empty before a check');
    check(await announcer.getAttribute('aria-live') === 'polite', 'announcer is polite');
    check(await page.getByTestId('update-check-status').getAttribute('role') === null, 'visual status block is not a live region');

    const header = page.locator('[data-check-action]');
    await header.focus();
    await page.keyboard.press('Enter');
    await page.getByTestId('update-check-current').getByText('Alpha game').waitFor();
    await page.waitForFunction(() => document.querySelector('[data-testid="update-check-announcer"]')?.textContent?.includes('started for 2 games'));
    check(await page.evaluate(() => document.activeElement?.hasAttribute('data-check-action') && document.activeElement.textContent.includes('Cancel')), 'focus stays on the header action (Cancel)');
    const bar = page.getByRole('progressbar', {name: 'Update check progress'});
    check(await bar.getAttribute('aria-valuemin') === '0', 'progressbar min');
    check(await bar.getAttribute('aria-valuemax') === '2', 'progressbar max');
    check(await bar.getAttribute('aria-valuenow') === '0', 'progressbar now');

    // Progress moves to the next game: the visible line changes, the announcer does not.
    await page.evaluate(() => { window.testStatus = {...window.testStatus, checked: 1, current: 'Beta game'}; });
    await page.getByTestId('update-check-current').getByText('Beta game').waitFor({timeout: 5000});
    check(await bar.getAttribute('aria-valuenow') === '1', 'progressbar follows the checked count');
    const spoken = await announcer.innerText();
    check(!spoken.includes('Alpha game') && !spoken.includes('Beta game'), `announcer never names the current game: ${spoken}`);

    // Blocked bulk buttons stay focusable and name their reason.
    await page.getByRole('checkbox', {name: /Select all/}).check();
    const bulk = page.getByRole('button', {name: 'Mark updated', exact: true});
    check(await bulk.getAttribute('aria-disabled') === 'true', 'bulk button is aria-disabled during a check');
    await bulk.focus();
    check(await bulk.evaluate(element => element === document.activeElement), 'blocked bulk button keeps focus');
    check(await page.getByTestId('bulk-disabled-reason').isVisible(), 'bulk reason is visible');
    check((await bulk.getAttribute('aria-describedby')) === 'bulk-disabled-reason', 'bulk button points at its reason');
    const mainButton = page.locator('[data-update-row="81"] .menu-button-main');
    check(await mainButton.getAttribute('aria-disabled') === null, 'download main stays usable during a check');
    await page.getByRole('checkbox', {name: /Select all/}).uncheck();

    // The check ends: one summary.
    await page.evaluate(() => {
      window.testStatus = {...window.testStatus, running: false, outcome: 'completed', checked: 2, current: '',
        results: [{id: 81, title: 'Alpha game', has_update: true, version: '2'}, {id: 82, title: 'Beta game', error: 'Thread not found'}]};
    });
    await page.waitForFunction(() => document.querySelector('[data-testid="update-check-announcer"]')?.textContent?.includes('Update check finished'), undefined, {timeout: 6000})
      .catch(async () => { throw new Error(`no finish announcement: ${await announcer.innerText()}`); });
    check((await announcer.innerText()).trim() === 'Update check finished: 2 of 2 games checked. 1 check failed. 1 update found.', `finish summary: ${await announcer.innerText()}`);
    check((await announcer.innerText()).startsWith(await page.getByTestId('update-check-summary').innerText()), 'announcer starts with the visible statement');

    // A running game blocks the download: the main button keeps focus and says why.
    await open();
    await page.evaluate(() => { window.pywebview = {api: {get_running_games: async () => [81]}}; });
    await page.waitForFunction(() => document.querySelector('[data-update-row="81"] .menu-button-main')?.getAttribute('aria-disabled') === 'true', undefined, {timeout: 15000})
      .catch(() => { throw new Error('running game did not block the main button'); });
    await mainButton.focus();
    check(await mainButton.evaluate(element => element === document.activeElement), 'blocked main button keeps focus');
    const reasonId = await mainButton.getAttribute('aria-describedby');
    check(!!reasonId && (await page.locator(`[id="${reasonId}"]`).innerText()).includes('Close the game'), 'main button reason');
    await mainButton.click({force: true});
    check(await page.getByRole('dialog', {name: 'Download update'}).count() === 0, 'blocked main button does nothing');

    // The picker: mirrors and torrents are radio groups with one selection.
    await page.evaluate(() => { localStorage.setItem('wlib-mock-rpdl', 'fixture'); });
    await open();
    await page.evaluate(() => {
      window.pywebview = {api: {find_download_offers: async () => ({success: true, offers: [{game_id: 81, archive_path: '/tmp/alpha.zip'}]})}};
      window.dispatchEvent(new CustomEvent('wlib-refresh-library'));
    });
    const row = page.locator('[data-update-row="81"]');
    await row.getByText('Install found archive').waitFor({timeout: 8000});
    await row.getByRole('button', {name: 'More update actions', exact: true}).click();
    await page.getByRole('menuitem', {name: 'Download update', exact: true}).click();
    const picker = page.getByRole('dialog', {name: 'Download update'});
    await picker.getByTestId('rpdl-export-actions').waitFor({timeout: 8000});
    const groups = picker.getByRole('radiogroup');
    check(await groups.count() >= 2, 'builds and torrents are radio groups');
    for (const group of await groups.all()) {
      const name = await group.evaluate(element => (element.getAttribute('aria-labelledby') || '').split(' ').map(id => document.getElementById(id)?.textContent?.trim()).join(' '));
      check(!!name, 'each radio group has a label');
    }
    check(await picker.getByRole('radio', {checked: true}).count() === 1, 'one radio is checked across all groups');
    check(await picker.locator('.mirror:not(.site-option)[aria-pressed]').count() === 0, 'cards use aria-checked, not aria-pressed');
    const first = groups.first();
    const radios = first.getByRole('radio');
    check(await radios.count() >= 2, 'first build has several mirrors');
    const stops = await first.evaluate(group => [...group.querySelectorAll('[role="radio"]')].filter(radio => radio.tabIndex === 0).length);
    check(stops === 1, `a radio group is one Tab stop (${stops})`);
    // Arrow keys move and check within the group.
    await radios.first().click();
    await radios.first().focus();
    await page.keyboard.press('ArrowRight');
    check(await radios.nth(1).getAttribute('aria-checked') === 'true', 'ArrowRight checks the next mirror');
    check(await radios.nth(1).evaluate(element => element === document.activeElement), 'ArrowRight moves focus');
    check(await picker.getByRole('radio', {checked: true}).count() === 1, 'still one checked radio');
    await page.keyboard.press('ArrowLeft');
    check(await radios.first().getAttribute('aria-checked') === 'true', 'ArrowLeft goes back');
    // A disabled radio is skipped.
    const radioCount = await radios.count();
    await radios.nth(1).evaluate(element => { element.disabled = true; });
    await page.keyboard.press('ArrowDown');
    const landing = radioCount >= 3 ? radios.nth(2) : radios.first();
    check(await landing.getAttribute('aria-checked') === 'true' && await landing.evaluate(element => element === document.activeElement), 'ArrowDown skips a disabled mirror');
    check(await radios.nth(1).getAttribute('aria-checked') === 'false', 'the disabled mirror is not checked');
    await radios.nth(1).evaluate(element => { element.disabled = false; });
    // Torrents share the one selection.
    const torrentGroup = picker.getByRole('radiogroup', {name: 'RPDL torrents'});
    await torrentGroup.getByRole('radio').first().click();
    check(await picker.getByRole('radio', {checked: true}).count() === 1, 'a torrent replaces the mirror choice');
    const snapshot = await torrentGroup.ariaSnapshot();
    check(snapshot.includes('radiogroup "RPDL torrents"') && snapshot.includes('radio') && snapshot.includes('[checked]'), `torrent tree: ${snapshot}`);

    // Esc closes; the menu item is gone, so focus lands on the row's split button.
    await page.keyboard.press('Escape');
    await picker.waitFor({state: 'detached'});
    await page.waitForFunction(() => !!document.activeElement?.closest('[data-update-row="81"] [data-update-row-action]'), undefined, {timeout: 3000})
      .catch(async () => { throw new Error(`focus after close: ${await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 120))}`); });

    check(errors.length === 0, `page errors: ${errors.join('; ')}`);
    return 'PASS: announcer, progressbar, blocked-button reasons, picker radio groups and focus return';
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      for (const [key, value] of previous) value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
    }, previous);
    await page.goto(previousUrl);
  }
}
