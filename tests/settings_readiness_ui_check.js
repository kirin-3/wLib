// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/settings_readiness_ui_check.js
// The Running games setup summary, the optional tool states, and what Settings hides until it applies.
async page => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const url = page.url();
  const keys = ['wlib-mock-platform', 'wlib-mock-settings', 'wlib-mock-umu', 'wlib-mock-wine', 'wlib-mock-gamescope'];
  const old = await page.evaluate(keys => keys.map(k => [k, localStorage.getItem(k)]), keys);
  let step = 'setup';
  const open = async (state) => {
    await page.evaluate(([keys, state]) => {
      delete window.pywebview;
      for (const key of keys) localStorage.removeItem(key);
      localStorage.setItem('wlib-mock-platform', state.platform || 'linux');
      localStorage.setItem('wlib-mock-settings', JSON.stringify(state.settings || {}));
      for (const [key, value] of Object.entries(state.storage || {})) localStorage.setItem(key, value);
    }, [keys, state]);
    await page.goto(new URL('/#/settings', url).toString()); await page.reload();
    await page.locator('#settings-search-desktop').waitFor();
  };
  const windowsLine = page.locator('[data-readiness-windows]');
  const settled = () => page.waitForFunction(() => {
    const line = document.querySelector('[data-readiness-windows]');
    return line && line.dataset.readinessWindows !== 'checking' && !document.querySelector('[data-readiness-optional] .animate-spin');
  });
  try {
    step = 'fresh install with Wine';
    await open({});
    await settled();
    check(/ready\. They run in System Wine/.test(await windowsLine.textContent()), 'Wine default is not shown as ready');
    check(await page.locator('[data-readiness-action]').count() === 0, 'A ready state offers an action');
    const optional = await page.locator('[data-readiness-optional]').textContent();
    check(/Optional tools: 0 of 6 installed/.test(optional) && /Games run without them/.test(optional), `Optional summary: ${optional}`);
    check(/Optional · not installed\. Only needed for Proton/.test(await page.locator('#umu').textContent()), 'umu is not shown as optional with Wine');
    check(await page.locator('#umu .ui-status-badge--warn').count() === 0, 'umu warns although Wine needs no umu');
    check(await page.locator('#sandbox .ui-status-badge--warn').count() === 0, 'A missing optional sandbox tool warns');
    check(/Optional · not installed/.test(await page.locator('#performance').textContent()), 'GameMode/MangoHud/Gamescope are not neutral optional');

    step = 'deep link shows the summary under the heading';
    await page.goto(new URL('/#/settings#running-games', url).toString());
    await page.waitForTimeout(1200);
    const gap = await page.evaluate(() => {
      const head = document.getElementById('running-games').firstElementChild.getBoundingClientRect();
      return document.querySelector('[data-testid="settings-readiness"]').getBoundingClientRect().top - head.bottom;
    });
    check(gap >= -2 && gap < 40, `The summary hides under the heading after a deep link (${gap}px)`);

    step = 'gamescope hidden until installed';
    check(await page.locator('#performance select, #performance input[type="text"]').count() === 0, 'Gamescope fields show while it is not installed');
    check(await page.getByRole('button', { name: 'Copy command' }).count() === 1, 'Gamescope install hint is missing');

    step = 'LC Plus hidden while LewdCorner is off';
    check(await page.locator('[data-testid="settings-lc-plus"]').count() === 0, 'LC Plus shows while LewdCorner is off');

    step = 'search opens a closed disclosure';
    const winetricksOpen = () => page.evaluate(() => [...document.querySelectorAll('#tools details[data-settings-disclosure]')].find(el => el.textContent.includes('corefonts'))?.open);
    check(await winetricksOpen() === false, 'Disclosure starts open');
    await page.locator('#settings-search-desktop').fill('corefonts');
    await page.waitForFunction(() => [...document.querySelectorAll('#tools details[data-settings-disclosure]')].find(el => el.textContent.includes('corefonts'))?.open === true);
    await page.locator('#settings-search-desktop').fill('');
    await page.waitForFunction(() => [...document.querySelectorAll('#tools details[data-settings-disclosure]')].find(el => el.textContent.includes('corefonts'))?.open === false);
    await page.locator('#settings-search-desktop').fill('steamrt');
    await page.locator('#umu').waitFor({ state: 'visible' });
    await page.locator('#settings-search-desktop').fill('');

    step = 'Proton runner without umu';
    await open({ settings: { proton_path: 'GE-Proton' } });
    await settled();
    check(/GE-Proton \(latest\) needs umu-launcher/.test(await windowsLine.textContent()), 'Proton without umu does not ask for umu');
    check(await page.locator('#umu .ui-status-badge--warn').count() === 1, 'umu does not warn when Proton needs it');
    await page.locator('[data-readiness-action]').click();
    await page.waitForFunction(() => document.activeElement?.closest('#umu') && document.activeElement.hasAttribute('data-readiness-target'));

    step = 'no system Wine';
    await open({ storage: { 'wlib-mock-wine': 'missing' } });
    await settled();
    check(/UMU-Proton needs umu-launcher\. Wine is not installed/.test(await windowsLine.textContent()), 'No-Wine default does not explain UMU-Proton');

    step = 'ready with umu, gamescope and LewdCorner';
    await open({ settings: { proton_path: 'GE-Proton', lc_enabled: true }, storage: { 'wlib-mock-umu': 'ready', 'wlib-mock-gamescope': 'installed' } });
    await settled();
    check(/ready\. They run in GE-Proton \(latest\) through umu-launcher/.test(await windowsLine.textContent()), 'Prepared umu is not ready');
    check(/Optional tools: 1 of 6 installed/.test(await page.locator('[data-readiness-optional]').textContent()), 'Gamescope is not counted');
    await page.locator('#performance fieldset select').first().waitFor();
    check(await page.locator('[data-testid="settings-lc-plus"]').count() === 1, 'LC Plus is hidden while LewdCorner is on');

    step = 'Windows has no summary';
    await open({ platform: 'windows' });
    await page.locator('#running-games #windows-tools').waitFor();
    check(await page.locator('[data-testid="settings-readiness"]').count() === 0, 'Windows shows the Linux setup summary');
    return 'PASS: setup summary (Wine, Proton without umu, no Wine, ready), neutral optional tools, hidden gamescope fields and LC Plus, search opens disclosures';
  } catch (error) { throw new Error(`${step}: ${error}`);
  } finally {
    await page.evaluate(old => { delete window.pywebview; for (const [k, v] of old) v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); }, old);
    await page.goto(url);
  }
}
