// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/updates_first_run_ui_check.js.
// The Updates page before and after the first check: "Not checked yet" only
// when no check ran and there are no update rows, the green up-to-date mark
// only after a real check, and no first-run hint above a list of updates.
async page => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const previousUrl = page.url();
  const keys = ['wlib-mock-games', 'wlib-theme', 'wlib-ui-motion', 'wlib-mock-last-update-check'];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const current = {id: 1, title: 'Current game', exe_path: '/game', version: '2', latest_version: '2', f95_url: 'https://f95zone.to/threads/current.1/'};
  const outdated = {id: 2, title: 'Outdated game', exe_path: '/game', version: '1', latest_version: '2', f95_url: 'https://f95zone.to/threads/outdated.2/'};
  const open = async (games, lastCheck) => {
    await page.evaluate(({games, lastCheck}) => {
      delete window.pywebview;
      localStorage.setItem('wlib-ui-motion', 'off');
      localStorage.setItem('wlib-mock-games', JSON.stringify(games));
      if (lastCheck) localStorage.setItem('wlib-mock-last-update-check', lastCheck);
      else localStorage.removeItem('wlib-mock-last-update-check');
    }, {games, lastCheck});
    await page.goto(new URL('/#/updates', previousUrl).toString());
    await page.reload();
    await page.getByTestId('updates-last-checked').waitFor();
  };
  const status = page.getByTestId('update-check-status');
  const notChecked = page.getByTestId('updates-not-checked');
  const upToDate = page.getByText('Your games are up to date.');
  try {
    // Never checked, no updates: one "Not checked yet" state, no green mark.
    await open([current], '');
    await notChecked.waitFor();
    check(await page.getByTestId('updates-last-checked').innerText().then(text => text.includes('Last check: never') && text.includes('Automatic check: ')), 'never: header says never and names the automatic check');
    check(await notChecked.getByRole('heading', {name: 'Not checked yet'}).isVisible(), 'never: heading');
    check(await notChecked.getByText(/one game every 2 seconds/).isVisible(), 'never: card explains the delay');
    check(await notChecked.getByRole('button', {name: 'Check all games'}).isEnabled(), 'never: card has the check button');
    check(await page.getByRole('button', {name: 'Check all games'}).count() === 1, 'never: one check button, not a second in the header');
    check(await upToDate.count() === 0, 'never: no up-to-date claim');
    check(await page.getByText('No updates available.').count() === 0, 'never: no "No updates available."');
    check((await status.innerText()).trim() === '', 'never: status block stays empty');

    // The card starts a check; focus moves to Cancel, the pace note shows,
    // and a finished check gives the green mark.
    await notChecked.getByRole('button', {name: 'Check all games'}).click();
    await page.getByTestId('update-check-pace').waitFor();
    check(await notChecked.count() === 0, 'running: first-run card leaves');
    check(await page.evaluate(() => document.activeElement?.hasAttribute('data-check-action')), 'running: focus moves to Cancel');
    await upToDate.waitFor({timeout: 10000});
    check(await page.getByTestId('updates-last-checked').innerText().then(text => text.includes('Last check: just now')), 'after check: header says just now');
    check(!!(await page.getByTestId('updates-last-checked').locator('time').getAttribute('title')), 'after check: full date in the title');

    // Checked before (a recorded full pass), no updates: green mark only.
    await open([current], '2026-11-20T10:00:00');
    await upToDate.waitFor();
    check(await notChecked.count() === 0, 'checked: no first-run card');
    check(await page.locator('[data-check-action]').getByText('Check all games').isVisible(), 'checked: header check button is back');

    // Never checked, but updates are known: the list, no first-run hint.
    await open([current, outdated], '');
    await page.getByRole('heading', {name: /^Updates available \(1\)/}).waitFor();
    check(await notChecked.count() === 0, 'never + updates: no first-run card');
    check(await page.getByText(/one game every 2 seconds/).count() === 0, 'never + updates: no hint above the list');
    check((await status.innerText()).trim() === '', 'never + updates: status block stays empty');
    check(await page.locator('[data-check-action]').getByText('Check all games').isVisible(), 'never + updates: header check button');

    // Marking the last update leaves the list: the first-run card replaces it
    // and focus goes to its button.
    await page.getByRole('button', {name: 'More update actions', exact: true}).last().click();
    await page.getByRole('menuitem', {name: 'Mark updated', exact: true}).click();
    await notChecked.waitFor();
    await page.waitForFunction(() => document.activeElement?.hasAttribute('data-first-check'), undefined, {timeout: 5000})
      .catch(() => { throw new Error('never + updates: focus did not move to the first-run button'); });

    check(errors.length === 0, `page errors: ${errors.join('; ')}`);
    return 'PASS: never-checked, checked and update-list states';
  } finally {
    await page.evaluate(previous => {
      for (const [key, value] of previous) value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
    }, previous);
    await page.goto(previousUrl);
  }
}
