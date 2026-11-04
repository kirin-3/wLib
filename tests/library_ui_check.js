// SPDX-License-Identifier: GPL-3.0-or-later
// Open the Vite app in an isolated playwright-cli browser session, then run:
// playwright-cli run-code --filename tests/library_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const nativeDialogs = [];
  const onDialog = dialog => { nativeDialogs.push(dialog.message()); void dialog.dismiss(); };
  page.on("dialog", onDialog);
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const previous = await page.evaluate(() => ({
    games: localStorage.getItem("wlib-mock-games"),
    state: localStorage.getItem("wlib-library-view-state"),
  }));
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", JSON.stringify([
        { id: 1, title: "Alpha", exe_path: "/games/alpha/game.exe", version: "1.0", latest_version: "2.0", engine: "Unity", last_played: "2026-10-01", launch_targets: [
          { id: 1, game_id: 1, label: "Alternate", exe_path: "/games/alpha/alt.exe", sort_order: 0 },
        ] },
        { id: 2, title: "Beta", exe_path: "/games/beta/game.exe", version: "1.0", latest_version: "2.0", engine: "Ren'Py", last_played: "2026-10-02" },
      ]));
      localStorage.setItem("wlib-library-view-state", JSON.stringify({
        layoutMode: "list", filterCollection: "Recently played", sortBy: "engine", sortDir: "asc", isFiltersCollapsed: false,
      }));
    });
    const libraryUrl = new URL(previousUrl);
    libraryUrl.hash = "/";
    await page.goto(libraryUrl.toString());
    await page.reload();
    await page.getByRole("button", { name: "Alpha", exact: true }).waitFor();
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-library-view-state")));
    check(saved.sortBy === "engine" && saved.sortDir === "asc", "Restoring Recently played must preserve the chosen sort");

    check(await page.getByRole("slider", { name: "Card size" }).count() === 0, "Card size must be hidden in list view");
    await page.getByRole("button", { name: "All Games", exact: true }).click();
    await page.getByRole("button", { name: "Recently played", exact: true }).click();
    check(await page.getByRole("button", { name: "Last played", exact: true }).locator("..").getAttribute("aria-sort") === "descending", "Selecting Recently played should default to newest first");
    await page.getByRole("button", { name: "All Games", exact: true }).click();
    await page.getByRole("button", { name: "Grid view", exact: true }).click();
    check(await page.getByRole("slider", { name: "Card size" }).count() === 1, "Card size must appear in grid view");
    await page.setViewportSize({ width: 2560, height: 1000 });
    const columns = [];
    for (const cardSize of [0, 4]) {
      await page.evaluate(cardSize => {
        const state = JSON.parse(localStorage.getItem("wlib-library-view-state"));
        localStorage.setItem("wlib-library-view-state", JSON.stringify({ ...state, cardSize }));
      }, cardSize);
      await page.reload();
      await page.getByRole("slider", { name: "Card size" }).waitFor();
      columns.push(await page.locator(".library-cards").evaluate(el => getComputedStyle(el).gridTemplateColumns.split(" ").length));
    }
    check(columns[0] > columns[1], "Largest saved card size must yield fewer grid columns than smallest");
    const card = page.getByRole("checkbox", { name: "Alpha. Press Enter for details.", exact: true });
    const targets = card.getByRole("button", { name: "Choose launch target for Alpha", exact: true });
    await targets.click();
    await page.keyboard.press("Escape");
    check(await card.getByRole("button", { name: "Alternate", exact: true }).count() === 0, "Escape should close launch targets");
    await targets.click();
    await card.getByRole("button", { name: "Play", exact: true }).click();
    await card.getByRole("button", { name: "Stop", exact: true }).waitFor();
    check(await card.getByRole("button", { name: "Alternate", exact: true }).count() === 0, "Starting a game must close its launch targets");
    await card.getByRole("button", { name: "Stop", exact: true }).click();

    // Hold an older response, complete a mutation, then deliver the older data.
    const deferRefresh = async () => page.evaluate(() => {
      const old = JSON.parse(localStorage.getItem("wlib-mock-games"));
      window.pendingRefresh = [];
      window.pywebview = { api: { get_games: () => new Promise(resolve => window.pendingRefresh.push(() => resolve(old))) } };
      window.dispatchEvent(new Event("wlib-refresh-library"));
      window.pywebview.api.get_games = () => JSON.parse(localStorage.getItem("wlib-mock-games"));
    });
    const releaseRefresh = async () => page.evaluate(async () => {
      window.pendingRefresh.forEach(resolve => resolve());
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
    });
    await deferRefresh();
    await card.click({ button: "right" });
    await page.getByRole("button", { name: "Mark as updated to 2.0", exact: true }).click();
    await page.getByRole("link", { name: "Updates, 1 available", exact: true }).waitFor();
    await releaseRefresh();
    check(await card.locator(".update-pill").count() === 0, "An old response must not restore the library update badge");
    check(await page.getByRole("link", { name: "Updates, 1 available", exact: true }).count() === 1, "An old response must not restore the navigation count");

    await page.getByRole("link", { name: "Updates, 1 available", exact: true }).click();
    await page.getByRole("button", { name: "Mark updated", exact: true }).waitFor();
    await deferRefresh();
    await page.getByRole("button", { name: "Mark updated", exact: true }).click();
    await page.getByRole("link", { name: "Updates, 0 available", exact: true }).waitFor();
    await releaseRefresh();
    check(await page.getByRole("heading", { name: /^Updates Available/ }).count() === 0, "An old response must not restore the Updates list");
    check(await page.getByRole("link", { name: "Updates, 0 available", exact: true }).count() === 1, "The zero update count must remain current");
    // Selection, bulk undo and delayed removal use a fresh browser mock fixture.
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-library-view-state", JSON.stringify({layoutMode: "grid"}));
      localStorage.setItem("wlib-mock-games", JSON.stringify([1,2,3].map(id => ({id, title: `Bulk ${id}`, exe_path: "/game", version: "1", latest_version: "2", play_status: id === 1 ? "Completed" : "Playing"}))));
    });
    await page.goto(new URL('/#/', previousUrl).toString());
    await page.reload();
    const cards = page.locator('[data-game-id]');
    await cards.first().waitFor();
    await page.evaluate(() => { window.__bulkLaunches = 0; window.pywebview = { api: { launch_game: async () => { window.__bulkLaunches++; return { success: true }; } } }; });
    for (const layout of ['Grid view', 'Banner view', 'List view', 'Compact view']) {
      await page.getByRole('button', {name: layout, exact: true}).click();
      await page.getByRole('button', {name: 'Select', exact: true}).click();
      await cards.first().click();
      await page.getByRole('region', {name: 'Bulk actions'}).getByText('1 selected').waitFor();
      const previousLaunches = await page.evaluate(() => window.__bulkLaunches);
      await cards.first().locator('button[title^="Play "]').first().click();
      await page.waitForFunction(previous => window.__bulkLaunches === previous + 1, previousLaunches);
      await page.getByRole('region', {name: 'Bulk actions'}).getByText('1 selected').waitFor();
      await page.getByRole('region', {name: 'Bulk actions'}).getByRole('button', {name: 'Done'}).click();
    }
    await page.evaluate(() => { delete window.pywebview; });
    await cards.nth(0).click({modifiers: ['Control']});
    await cards.nth(2).click({modifiers: ['Shift']});
    const bar = page.getByRole('region', {name: 'Bulk actions'});
    await bar.getByText('3 selected').waitFor();
    await page.getByLabel('Search games').fill('Bulk 1');
    await bar.getByText('1 selected').waitFor();
    await page.getByLabel('Search games').fill('');
    await bar.getByRole('button', {name: 'Select all'}).click();
    await bar.getByText('3 selected').waitFor();
    await bar.getByLabel('Set status').selectOption('Completed');
    await page.getByText('2 changed, 1 skipped.').waitFor();
    await page.getByRole('button', {name: 'Undo', exact: true}).last().click();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('wlib-mock-games')).every(game => game.play_status === (game.id === 1 ? 'Completed' : 'Playing')));
    await bar.getByRole('button', {name: 'Remove', exact: true}).click();
    await page.getByRole('button', {name: 'Undo', exact: true}).last().waitFor();
    await page.evaluate(() => window.dispatchEvent(new Event('wlib-refresh-library')));
    await page.waitForTimeout(200);
    check(await cards.count() === 0, 'Refresh must not restore pending removals');
    await page.getByRole('link', {name: 'Updates, 0 available'}).click();
    check(await page.getByRole('heading', {name: /^Updates Available/}).count() === 0, 'Pending games must leave Updates');
    await page.getByRole('link', {name: 'Library', exact: true}).click();
    await page.getByRole('button', {name: 'Undo', exact: true}).last().click();
    await cards.first().waitFor();
    await cards.first().click();
    await page.getByRole('dialog').getByRole('button', {name: 'More actions', exact: true}).click();
    await page.getByRole('dialog').getByRole('menuitem', {name: 'Remove from library', exact: true}).click();
    await page.reload();
    await cards.first().waitFor();
    check(JSON.parse(await page.evaluate(() => localStorage.getItem('wlib-mock-games'))).length === 3, 'Reload must keep pending removal');
    await cards.first().click({button: 'right'});
    await page.getByRole('dialog', {name: /Actions for/}).getByRole('button', {name: 'Remove', exact: true}).click();
    await page.mouse.move(0, 0);
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('wlib-mock-games')).length === 2, undefined, {timeout: 12000});
    check(nativeDialogs.length === 0, `Unexpected native dialogs: ${nativeDialogs.join(", ")}`);
    return "PASS: saved sorting, launch menus, and delayed refreshes in Library, Updates and navigation";
  } finally {
    page.off("dialog", onDialog);
    await page.evaluate(({ games, state }) => {
      delete window.pywebview;
      delete window.pendingRefresh;
      for (const [key, value] of [["wlib-mock-games", games], ["wlib-library-view-state", state]]) {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
      }
    }, previous);
    if (previousViewport) await page.setViewportSize(previousViewport);
    await page.goto(previousUrl);
  }
}
