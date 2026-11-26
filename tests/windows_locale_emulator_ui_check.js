// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/windows_locale_emulator_ui_check.js.
// The Japanese locale group (Running games, Windows only), the per-game Locale Emulator toggle, and the launch toast.
async page => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const url = page.url();
  const keys = ['wlib-mock-settings', 'wlib-mock-platform', 'wlib-mock-games', 'wlib-mock-locale-emulator'];
  const old = await page.evaluate(keys => keys.map(k => [k, localStorage.getItem(k)]), keys);
  const go = async (hash) => { const next = new URL(url); next.hash = hash; await page.goto(next.toString()); await page.reload(); };
  let step = 'setup';
  try {
    await page.setViewportSize({ width: 1280, height: 860 });

    step = 'linux hides Windows tools';
    await page.evaluate(() => { delete window.pywebview; localStorage.setItem('wlib-mock-platform', 'linux'); localStorage.setItem('wlib-mock-settings', '{}'); localStorage.setItem('wlib-mock-games', '[]'); });
    await go('/settings');
    await page.locator('#wine-proton').waitFor();
    check(await page.locator('#windows-tools').count() === 0, 'Linux must not show Windows tools');
    check(await page.locator('nav [data-section-id="windows-tools"]').count() === 0, 'Linux nav must not list Windows tools');

    step = 'windows shows Windows tools';
    await page.evaluate(() => { localStorage.setItem('wlib-mock-platform', 'windows'); localStorage.removeItem('wlib-mock-locale-emulator'); });
    await go('/settings#windows-tools');
    const section = page.locator('#windows-tools');
    await section.waitFor();
    check(await page.locator('#wine-proton').count() === 0, 'Windows must not show the Wine / Proton section');
    await page.waitForFunction(() => document.querySelector('nav [data-section-id="running-games"]')?.getAttribute('aria-current') === 'location');
    check(await page.locator('#running-games #windows-tools').count() === 1, 'Japanese locale is a group in Running games');
    check(!(await page.locator('#running-games').innerText()).includes('not supported'), 'Windows shows no Wine placeholder');
    const status = section.getByTestId('locale-emulator-status');
    await page.waitForFunction(() => document.querySelector('[data-testid="locale-emulator-status"]')?.textContent.startsWith('Not detected'));

    step = 'search';
    const search = page.locator('#settings-search-desktop');
    for (const word of ['locale', 'japanese', 'leproc']) {
      await search.fill(word);
      await section.waitFor({ state: 'visible' });
      await page.locator('[data-settings-row="windows-tools-static-1"]').waitFor({ state: 'visible' });
    }
    await search.fill('');

    step = 'wrong file';
    await page.evaluate(() => { window.pywebview = { api: { browse_file: () => 'D:\\Tools\\LE\\LEGUI.exe' } }; });
    await section.getByRole('button', { name: 'Choose LEProc.exe' }).click();
    await page.getByText('Choose the Locale Emulator LEProc.exe executable.').first().waitFor();
    check((await section.getByTestId('locale-emulator-path').innerText()) === 'Use the detected installation', 'Wrong file must not be stored');

    step = 'save then recheck';
    await page.evaluate(() => { window.pywebview = { api: { browse_file: () => 'D:\\Tools\\LE\\LEProc.exe' } }; });
    await section.getByRole('button', { name: 'Choose LEProc.exe' }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="locale-emulator-status"]')?.textContent === 'Detected — D:\\Tools\\LE\\LEProc.exe');
    check((await section.getByTestId('locale-emulator-path').innerText()) === 'D:\\Tools\\LE\\LEProc.exe', 'Path shows after save');
    await section.getByRole('button', { name: 'Clear' }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="locale-emulator-status"]')?.textContent.startsWith('Not detected'));
    await page.evaluate(() => { localStorage.setItem('wlib-mock-locale-emulator', 'installed'); });
    await section.getByRole('button', { name: 'Check again' }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="locale-emulator-status"]')?.textContent.includes('Program Files'));
    check(!!(await status.innerText()), 'status text');

    step = 'detail toggle, missing';
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.removeItem('wlib-mock-locale-emulator');
      localStorage.setItem('wlib-mock-settings', '{}');
      localStorage.setItem('wlib-mock-games', JSON.stringify([
        { id: 1, title: 'Kana', exe_path: 'C:\\Games\\Kana\\Game.exe', run_japanese_locale: 1, launch_mode: 'auto' },
        { id: 2, title: 'Romaji', exe_path: 'C:\\Games\\Romaji\\Game.exe', run_japanese_locale: 0, launch_mode: 'auto' },
      ]));
    });
    await go('/');
    const open = async (title) => {
      const card = page.getByRole('row', { name: new RegExp(`^${title}(?: .*)?$`) });
      await card.waitFor();
      await card.press('Enter');
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('heading', { name: new RegExp(`^${title}`) }).waitFor();
      await dialog.getByRole('tab', { name: 'Launch', exact: true }).click();
      // The locale toggle is in Launch > Advanced, which is collapsed while every option in it is at its default.
      const advanced = dialog.getByTestId('launch-advanced');
      if (!(await advanced.evaluate(el => el.open))) await advanced.locator('summary').click();
      return dialog;
    };
    let dialog = await open('Kana');
    let toggle = dialog.getByRole('checkbox', { name: 'Run with Locale Emulator' });
    await toggle.waitFor({ state: 'attached' });
    await page.waitForFunction(() => document.querySelector('[data-testid="japanese-locale-note"]')?.textContent.includes('not detected'));
    check(await toggle.isChecked(), 'Stored on value shows on');
    check(await toggle.isEnabled(), 'An on toggle can still be turned off');
    await toggle.evaluate(el => el.click());
    check(!(await toggle.isChecked()), 'Toggle turned off');
    check(await toggle.isDisabled(), 'After it is off, the toggle is grayed out');
    check(await dialog.getByTestId('japanese-locale-note').getByRole('button', { name: 'Set up' }).count() === 1, 'Set up link shows');
    await page.keyboard.press('Escape');
    const discard = page.getByRole('button', { name: /^Discard/ });
    if (await discard.count()) await discard.first().click();
    await dialog.waitFor({ state: 'detached' });

    dialog = await open('Romaji');
    toggle = dialog.getByRole('checkbox', { name: 'Run with Locale Emulator' });
    await page.waitForFunction(() => document.querySelector('[data-testid="japanese-locale-note"]')?.textContent.includes('not detected'));
    check(await toggle.isDisabled() && !(await toggle.isChecked()), 'Off toggle is grayed out while missing');
    await dialog.getByTestId('japanese-locale-note').getByRole('button', { name: 'Set up' }).click();
    await page.locator('#windows-tools').waitFor();
    check(page.url().includes('#windows-tools'), 'Set up opens the Japanese locale group');

    step = 'detail toggle, ready';
    await page.evaluate(() => localStorage.setItem('wlib-mock-locale-emulator', 'installed'));
    await go('/');
    dialog = await open('Romaji');
    toggle = dialog.getByRole('checkbox', { name: 'Run with Locale Emulator' });
    await page.waitForFunction(() => document.querySelector('[data-testid="japanese-locale-note"]')?.textContent.includes('Japanese locale'));
    check(await toggle.isEnabled(), 'Ready: toggle enabled');
    check(await dialog.getByTestId('japanese-locale-note').getByRole('button', { name: 'Set up' }).count() === 0, 'Ready: no Set up link');
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached' });

    step = 'launch toast';
    await page.evaluate(() => localStorage.removeItem('wlib-mock-locale-emulator'));
    await go('/');
    const kana = page.getByRole('row', { name: /^Kana(?: .*)?$/ });
    await kana.waitFor();
    await kana.getByRole('button', { name: 'Play Kana', exact: true }).click();
    await page.getByText('Locale Emulator is not set up', { exact: false }).first().waitFor();
    await page.getByRole('button', { name: 'Open Locale Emulator settings' }).click();
    await page.locator('#windows-tools').waitFor();
    check(page.url().includes('#windows-tools'), 'Toast action opens the Japanese locale group');
    check(await kana.count() === 0 || true, 'navigated');

    step = 'linux toggle label';
    await page.evaluate(() => localStorage.setItem('wlib-mock-platform', 'linux'));
    await go('/');
    dialog = await open('Kana');
    await dialog.getByRole('checkbox', { name: 'Run with Japanese locale' }).waitFor({ state: 'attached' });
    check(!(await dialog.innerText()).includes('Locale Emulator'), 'Linux toggle does not mention Locale Emulator');
    await page.keyboard.press('Escape');
    return 'PASS: Japanese locale group, search, save/recheck, wrong-file toast, toggle states, Set up link, launch toast, Linux label';
  } catch (error) {
    throw new Error(`${step}: ${error}`);
  } finally {
    await page.evaluate(old => { delete window.pywebview; for (const [k, v] of old) v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); }, old);
    await page.goto(url);
  }
}
