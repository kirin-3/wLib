// SPDX-License-Identifier: GPL-3.0-or-later
// Run with playwright-cli run-code --filename tests/updates_narrow_ui_check.js.
// The Updates page and the download picker in a 390 px window: a long new
// version stays inside its row, and the picker footer keeps every button
// inside the dialog, on one line each, with the primary button last and full
// width. Measures element boxes, so it runs in both themes.
async page => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const previousUrl = page.url();
  const previousSize = page.viewportSize();
  const keys = ['wlib-mock-games', 'wlib-theme', 'wlib-mock-platform', 'wlib-ui-motion', 'wlib-mock-rpdl', 'wlib-mock-settings', 'wlib-mock-last-update-check'];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const games = [
    {id: 71, title: 'Download fixture', exe_path: '/fixture/game.sh', version: '1.0', latest_version: '2.0', f95_url: 'https://f95zone.to/threads/71/'},
    {id: 72, title: 'A Much Longer Game Title That Might Wrap Across Lines [Ep. 4]', developer: 'A Developer With A Long Studio Name', exe_path: '/fixture/b.exe', version: '0.4.1a', latest_version: '0.5.0 Public Final', f95_url: 'https://f95zone.to/threads/72/'},
  ];
  // Boxes of the footer buttons against the dialog and the viewport, and the
  // number of text lines in each label.
  const footerBoxes = () => {
    const dialog = document.querySelector('[role=dialog]').getBoundingClientRect();
    return [...document.querySelectorAll('[data-testid=update-download-footer] > button')].map(button => {
      const box = button.getBoundingClientRect();
      const tops = new Set();
      const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!node.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) if (rect.width > 1) tops.add(Math.round(rect.top / 4));
      }
      return {
        label: button.textContent.trim(), left: box.left, right: box.right, width: box.width, top: box.top, lines: tops.size,
        dialogLeft: dialog.left, dialogRight: dialog.right, viewport: innerWidth,
      };
    });
  };
  const checkFooter = (boxes, tag) => {
    check(boxes.length >= 2, `${tag}: footer has Close and an action`);
    for (const box of boxes) {
      check(box.left >= box.dialogLeft - 0.5 && box.right <= box.dialogRight + 0.5, `${tag}: "${box.label}" is inside the dialog (${Math.round(box.left)}-${Math.round(box.right)})`);
      check(box.left >= 0 && box.right <= box.viewport, `${tag}: "${box.label}" is inside the window`);
      check(box.lines === 1, `${tag}: "${box.label}" label stays on one line`);
    }
    const primary = boxes[boxes.length - 1];
    check(boxes.every(box => box.top <= primary.top), `${tag}: the primary button is the last row`);
    check(primary.width >= (primary.dialogRight - primary.dialogLeft) * 0.8, `${tag}: the primary button takes the full row`);
  };
  try {
    await page.setViewportSize({width: 390, height: 844});
    for (const theme of ['light', 'dark']) {
      await page.evaluate(({theme, games}) => {
        delete window.pywebview;
        localStorage.setItem('wlib-theme', theme);
        localStorage.setItem('wlib-mock-platform', 'linux');
        localStorage.setItem('wlib-ui-motion', 'off');
        localStorage.setItem('wlib-mock-games', JSON.stringify(games));
        localStorage.setItem('wlib-mock-last-update-check', '2026-11-20T10:00:00');
        localStorage.removeItem('wlib-mock-rpdl');
      }, {theme, games});
      await page.goto(new URL('/#/updates', previousUrl).toString());
      await page.reload();
      const version = page.locator('[data-update-row="72"]').getByTestId('update-version-change');
      await version.waitFor();
      const fit = await version.evaluate(element => {
        const row = element.closest('[data-update-row]').getBoundingClientRect();
        const column = element.parentElement.getBoundingClientRect();
        const parts = [...element.querySelectorAll('span')].map(span => span.getBoundingClientRect());
        return {right: Math.max(...parts.map(part => part.right)), column: column.right, row: row.right, text: element.innerText.replace(/\s+/g, ' ').trim()};
      });
      check(fit.right <= fit.column + 0.5 && fit.right <= fit.row, `${theme}: long version stays inside its row (${Math.round(fit.right)} > ${Math.round(fit.column)})`);
      check(/0\.4\.1a\s*→\s*(to\s+)?0\.5\.0 Public Final/.test(fit.text), `${theme}: version still reads old → new (${fit.text})`);

      // A mirror picked: Close and Open in browser share a row, Download under them.
      await page.getByRole('button', {name: 'Download update', exact: true}).first().click();
      const picker = page.getByRole('dialog', {name: 'Download update'});
      await picker.getByText('Windows/Linux').waitFor();
      await picker.getByRole('button', {name: 'Download', exact: true}).waitFor();
      const mirrorBoxes = await page.evaluate(footerBoxes);
      checkFooter(mirrorBoxes, `${theme} mirror`);
      const [close, browser] = mirrorBoxes;
      check(close.label === 'Close' && browser.label === 'Open in browser' && Math.abs(close.top - browser.top) < 1, `${theme} mirror: Close and Open in browser share a row`);
      await picker.locator('[data-testid=update-download-footer]').getByRole('button', {name: 'Close', exact: true}).click();
      await picker.waitFor({state: 'detached'});
    }

    // A torrent picked with a watch folder: Close, the in-app link, then Send to client.
    await page.evaluate(() => {
      localStorage.setItem('wlib-mock-rpdl', 'tester');
      const settings = JSON.parse(localStorage.getItem('wlib-mock-settings') || '{}');
      localStorage.setItem('wlib-mock-settings', JSON.stringify({...settings, torrent_watch_folder: '/home/user/watch'}));
    });
    await page.reload();
    await page.getByRole('button', {name: 'Download update', exact: true}).first().click();
    const picker = page.getByRole('dialog', {name: 'Download update'});
    await picker.locator('button.torrent').first().click();
    await picker.getByRole('button', {name: 'Send to client', exact: true}).waitFor();
    const torrentBoxes = await page.evaluate(footerBoxes);
    checkFooter(torrentBoxes, 'torrent');
    check(torrentBoxes.some(box => box.label === 'Download in wLib instead'), 'torrent: in-app link shows');

    check(errors.length === 0, `page errors: ${errors.join('; ')}`);
    return 'PASS: long versions and the download picker footer fit a 390 px window';
  } finally {
    await page.evaluate(previous => {
      for (const [key, value] of previous) value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value);
    }, previous);
    if (previousSize) await page.setViewportSize(previousSize);
    await page.goto(previousUrl);
  }
}
