// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/updates_failed_checks_ui_check.js.
// A finished check with failed checks: one outcome statement that the
// announcer repeats, tiles that match it, failed checks in plain words with
// the raw text kept, and "Re-check failed (n)" that checks only those games
// and is blocked (focusable, with a reason) while Download selected runs.
async page => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const previousUrl = page.url();
  const keys = ['wlib-mock-games', 'wlib-ui-motion', 'wlib-mock-platform', 'wlib-mock-update-errors'];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const games = [
    {id: 91, title: 'Alpha game', exe_path: '/a', version: '1', latest_version: '2', f95_url: 'https://f95zone.to/threads/alpha.91/'},
    {id: 92, title: 'Beta game', exe_path: '/b', version: '1', latest_version: '1', f95_url: 'https://f95zone.to/threads/beta.92/'},
    {id: 93, title: 'Gamma game', exe_path: '/c', version: '1', latest_version: '1', f95_url: 'https://f95zone.to/threads/gamma.93/'},
    {id: 94, title: 'Delta game', exe_path: '/d', version: '1', latest_version: '1', f95_url: ''},
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
    await open();
    await page.evaluate(() => {
      window.checkCalls = [];
      window.testStatus = {running: false, cancelling: false, outcome: 'idle', error: '', total: 0, checked: 0, current: '', results: [], delay_seconds: 2};
      window.pywebview = {api: {
        check_all_updates: async (ids) => {
          window.checkCalls.push(ids ?? null);
          window.testStatus = {...window.testStatus, running: true, outcome: 'running', total: ids ? ids.length : 4, checked: 0, current: 'Alpha game', results: []};
          return {success: true, total: window.testStatus.total, delay_seconds: 2};
        },
        get_update_status: async () => window.testStatus,
      }};
    });
    await page.locator('[data-check-action]').click();
    await page.getByTestId('update-check-current').waitFor();
    // The backend reports "failed" when every game was read but some checks failed.
    await page.evaluate(() => {
      window.testStatus = {...window.testStatus, running: false, outcome: 'failed', checked: 4, current: '',
        error: 'Some games could not be checked; see results for details',
        results: [
          {id: 91, title: 'Alpha game', has_update: true, latest_version: '2', error: '', error_code: ''},
          {id: 92, title: 'Beta game', has_update: false, error: 'HTTP Error 429: Too Many Requests', error_code: 'scraper_error'},
          {id: 93, title: 'Gamma game', has_update: false, error: 'Thread not found; it may have been deleted or moved', error_code: 'not_found'},
          {id: 94, title: 'Delta game', has_update: false, error: 'Login is required to access this thread', error_code: 'login_required', browser_fallback: 'logged_out'},
        ]};
    });
    const summary = page.getByTestId('update-check-summary');
    await summary.waitFor({timeout: 6000});
    const statement = (await summary.innerText()).trim();
    check(statement === 'Update check finished: 4 of 4 games checked. 3 checks failed.', `statement: ${statement}`);
    const statusText = await page.getByTestId('update-check-status').innerText();
    check(!/Check (completed|failed):/.test(statusText) && !statusText.includes('4/4'), `one statement, no duplicate: ${statusText}`);
    await page.waitForFunction(() => document.querySelector('[data-testid="update-check-announcer"]')?.textContent?.includes('Update check finished'), undefined, {timeout: 4000});
    const spoken = (await announcer.innerText()).trim();
    check(spoken === `${statement} 1 update found.`, `announcer agrees with the statement: ${spoken}`);

    // The tiles use the same numbers as the statement.
    check((await page.getByTestId('results-checked').innerText()).trim() === '4', 'Games checked tile is 4, as in "4 of 4"');
    check((await page.getByTestId('results-updates').innerText()).trim() === '1', 'Updates found tile');
    check((await page.getByTestId('results-failed').innerText()).trim() === '3', 'Checks failed tile');

    // Failed checks in plain words; raw text in the title, technical text stays visible.
    const items = page.getByTestId('failed-check');
    check(await items.count() === 3, 'three failed checks');
    const beta = items.filter({hasText: 'Beta game'});
    check((await beta.innerText()).includes('F95Zone limited the number of requests. Try again later.'), `429 words: ${await beta.innerText()}`);
    check((await beta.innerText()).includes('(HTTP Error 429: Too Many Requests)'), 'raw HTTP text stays visible');
    const gamma = items.filter({hasText: 'Gamma game'});
    check((await gamma.innerText()).includes('F95Zone thread not found.'), 'not found words');
    check(await gamma.locator('[title]').getAttribute('title') === 'Thread not found; it may have been deleted or moved', 'raw not-found text in title');
    const delta = items.filter({hasText: 'Delta game'});
    check((await delta.innerText()).includes('This thread needs an F95Zone login. Log in to F95Zone in the browser with the wLib extension.'), 'login words');

    // Re-check failed counts only failed games with an update thread (Delta has none).
    const recheck = page.getByTestId('recheck-failed');
    check((await recheck.innerText()).trim() === 'Re-check failed (2)', `button text: ${await recheck.innerText()}`);
    check(await recheck.getAttribute('aria-disabled') === null, 'Re-check failed is usable');
    await recheck.focus();
    await page.keyboard.press('Enter');
    await page.getByTestId('update-check-current').waitFor();
    const calls = await page.evaluate(() => window.checkCalls);
    check(JSON.stringify(calls[calls.length - 1]) === JSON.stringify([92, 93]), `re-checks only failed games: ${JSON.stringify(calls)}`);
    check(await page.evaluate(() => document.activeElement?.hasAttribute('data-check-action')), 'focus moves to Cancel');

    // A stopped check: one statement, the same in the announcer.
    await page.evaluate(() => { window.testStatus = {...window.testStatus, running: false, outcome: 'cancelled', checked: 1, error: '', results: []}; });
    await summary.waitFor({timeout: 6000});
    check((await summary.innerText()).trim() === 'Update check stopped: 1 of 2 games checked.', `stopped: ${await summary.innerText()}`);
    await page.waitForFunction(() => document.querySelector('[data-testid="update-check-announcer"]')?.textContent?.includes('stopped'), undefined, {timeout: 4000});

    // A failed check: the reason in plain words, the raw text in the title.
    await page.locator('[data-check-action]').click();
    await page.getByTestId('update-check-current').waitFor();
    await page.evaluate(() => { window.testStatus = {...window.testStatus, running: false, outcome: 'failed', checked: 0, error: 'Rate limited by F95Zone; try again later.', results: []}; });
    const reason = page.getByTestId('update-check-failure-reason');
    await reason.waitFor({timeout: 6000});
    check((await summary.innerText()).includes('Update check failed: 0 of 4 games checked.'), `failed: ${await summary.innerText()}`);
    check((await reason.innerText()).trim() === 'F95Zone limited the number of requests. Try again later.', `reason: ${await reason.innerText()}`);
    check(await reason.getAttribute('title') === 'Rate limited by F95Zone; try again later.', 'raw error in title');
    await page.waitForFunction(() => document.querySelector('[data-testid="update-check-announcer"]')?.textContent?.includes('Update check failed'), undefined, {timeout: 4000});
    check((await announcer.innerText()).includes('F95Zone limited the number of requests.'), 'announcer gives the same reason');

    // Download selected blocks Re-check failed: focusable, with its reason.
    await page.locator('[data-check-action]').click();
    await page.getByTestId('update-check-current').waitFor();
    await page.evaluate(() => {
      window.testStatus = {...window.testStatus, running: false, outcome: 'failed', checked: 4, total: 4, error: 'Some games could not be checked; see results for details',
        results: [{id: 92, title: 'Beta game', has_update: false, error: 'F95Zone request timed out', error_code: 'navigation_timeout'}]};
      let release;
      window.linksGate = new Promise(resolve => { release = resolve; });
      window.releaseLinks = release;
      window.pywebview.api.get_update_download_links = async () => { await window.linksGate; return {success: false, error: 'stub'}; };
      window.pywebview.api.close_update_download_links = async () => ({success: true});
    });
    await recheck.waitFor({timeout: 6000});
    check((await items.first().innerText()).includes('F95Zone did not answer in time. Try again later.'), 'timeout words');
    await page.getByRole('checkbox', {name: /Select all/}).check();
    await page.getByRole('button', {name: 'Download selected', exact: true}).click();
    await page.getByTestId('bulk-download-progress').waitFor();
    check(await recheck.getAttribute('aria-disabled') === 'true', 'Re-check failed is blocked during Download selected');
    await recheck.focus();
    check(await recheck.evaluate(element => element === document.activeElement), 'blocked Re-check failed keeps focus');
    const reasonId = await recheck.getAttribute('aria-describedby');
    check(!!reasonId && (await page.locator(`[id="${reasonId}"]`).innerText()).includes('Download selected'), 'blocked reason is described');
    const before = await page.evaluate(() => window.checkCalls.length);
    await recheck.click({force: true});
    check(await page.evaluate(() => window.checkCalls.length) === before, 'blocked Re-check failed does nothing');
    await page.evaluate(() => window.releaseLinks());
    await page.getByTestId('bulk-download-progress').waitFor({state: 'detached', timeout: 8000});

    check(errors.length === 0, `page errors: ${errors.join('; ')}`);
    return 'PASS: outcome statement, matching tiles and announcer, plain failed-check words, Re-check failed';
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      for (const [key, value] of previous) value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
    }, previous);
    await page.goto(previousUrl);
  }
}
