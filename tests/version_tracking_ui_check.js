// SPDX-License-Identifier: GPL-3.0-or-later
// Run against Vite in an isolated playwright-cli browser session.
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const viewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-play-sessions", "wlib-mock-platform", "wlib-mock-settings", "wlib-library-view-state", "wlib-theme", "wlib-ui-motion"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const errors = [];
  const onError = error => errors.push(String(error));
  page.on("pageerror", onError);
  const open = () => page.getByRole("group", { name: "Journal fixture. Press Enter for details.", exact: true }).press("Enter");
  const dialog = page.getByRole("dialog");
  const close = () => dialog.getByRole("button", { name: "Close", exact: true }).click();
  const panel = () => dialog.getByRole("tabpanel", { name: "Journal", exact: true });
  const badges = () => page.locator(".unplayed-label:visible");
  const updatePills = () => page.locator(".update-pill:visible");
  const contrast = async locator => locator.evaluate(element => {
    const luminance = rgb => {
      const channels = rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
        value /= 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    };
    let background = element;
    while (background && ["rgba(0, 0, 0, 0)", "transparent"].includes(getComputedStyle(background).backgroundColor)) background = background.parentElement;
    const a = luminance(getComputedStyle(element).color);
    const b = luminance(getComputedStyle(background || document.documentElement).backgroundColor);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });
  const evidence = [];
  try {
    for (const theme of ["dark", "light"]) {
      await page.evaluate(theme => {
        delete window.pywebview;
        localStorage.setItem("wlib-theme", theme);
        localStorage.setItem("wlib-ui-motion", "off");
        localStorage.setItem("wlib-mock-platform", "linux");
        localStorage.removeItem("wlib-library-view-state");
        localStorage.setItem("wlib-mock-games", JSON.stringify([
          { id: 71, title: "Journal fixture", exe_path: "/fixture/game.sh", version: "0.8", latest_version: "0.9", last_played_version: "0.7", f95_url: "https://f95zone.to/threads/71/", playtime_seconds: 600 },
          { id: 72, title: "Never played fixture", exe_path: "/fixture/new.sh", version: "0.8", latest_version: "0.8" },
          { id: 73, title: "Wishlist fixture", exe_path: "", version: "0.8", latest_version: "0.9", last_played_version: "0.7" },
        ]));
        localStorage.setItem("wlib-mock-play-sessions", JSON.stringify([
          { id: 71, game_id: 71, version: "0.7", started_at: "2026-10-20T10:00:00", ended_at: "2026-10-20T10:05:00", duration_seconds: 300 },
        ]));
      }, theme);
      await page.goto(new URL("/#/", previousUrl).toString());
      await page.reload();
      for (const layout of ["Grid", "List", "Compact"]) {
        await page.getByTitle(`${layout} View`, { exact: true }).click();
        await badges().waitFor();
        check(await badges().count() === 1, `${theme}/${layout}: only played installed games get an Unplayed badge`);
        check(await updatePills().count() === 1, `${theme}/${layout}: update indicator coexists with Unplayed`);
        check(await contrast(badges()) >= 4.5, `${theme}/${layout}: badge contrast must pass AA`);
      }
      await page.getByTitle("Grid View", { exact: true }).click();
      await page.getByRole("button", { name: "Filters", exact: true }).click();
      await page.getByRole("button", { name: "Unplayed update", exact: true }).click();
      await page.reload();
      await badges().waitFor();
      check(await badges().count() === 1, "Unplayed collection must restore on reload");
      await page.getByRole("button", { name: "All Games", exact: true }).click();
      await open();
      await dialog.getByRole("button", { name: "Skip version 0.9", exact: true }).click();
      await dialog.getByText("Version 0.9 skipped", { exact: true }).waitFor();
      check(await dialog.getByText("Update: 0.9", { exact: true }).count() === 0, "Skipped state replaces the modal update badge");
      await close();
      check(await updatePills().count() === 0, "Skipping clears the library update indicator");
      check(await page.getByRole("link", { name: "Updates, 0 available", exact: true }).count() === 1, "Skipping clears the navigation count");
      await page.getByRole("link", { name: "Updates, 0 available", exact: true }).click();
      check(await page.getByTitle("Open Journal fixture", { exact: true }).count() === 0, "Skipped game leaves the Updates view");
      await page.getByRole("link", { name: "Library", exact: true }).click();
      await page.getByRole("button", { name: "Updates available", exact: false }).click();
      await page.getByRole("group", { name: "Journal fixture. Press Enter for details.", exact: true }).waitFor({ state: "hidden" });
      await page.getByRole("button", { name: "All Games", exact: true }).click();
      const card = page.getByRole("group", { name: "Journal fixture. Press Enter for details.", exact: true });
      await card.press("Shift+F10");
      check(await page.getByRole("button", { name: "Mark as updated to 0.9", exact: true }).count() === 0, "Mark as updated is hidden while skipping");
      await page.getByRole("button", { name: "Stop skipping 0.9", exact: true }).click();
      await updatePills().waitFor();
      await card.press("Shift+F10");
      await page.getByRole("button", { name: "Skip version 0.9", exact: true }).click();
      await open();
      await dialog.getByRole("button", { name: "Stop skipping 0.9", exact: true }).click();
      await dialog.getByText("Update: 0.9", { exact: true }).waitFor();
      await dialog.getByRole("tab", { name: "Journal", exact: true }).click();
      await panel().getByText("Before journal", { exact: true }).waitFor();
      check(await contrast(panel()) >= 4.5, "Journal primary text contrast must pass AA");
      check(await contrast(panel().locator(".journal-version p")) >= 4.5, "Journal secondary text contrast must pass AA");
      await dialog.getByRole("button", { name: "Play", exact: true }).click();
      await page.evaluate(() => {
        const sessions = JSON.parse(localStorage.getItem("wlib-mock-play-sessions"));
        sessions.unshift({ id: 72, game_id: 71, version: "0.8", started_at: "2026-10-22T10:00:00", ended_at: null, duration_seconds: 300 });
        localStorage.setItem("wlib-mock-play-sessions", JSON.stringify(sessions));
        const games = JSON.parse(localStorage.getItem("wlib-mock-games"));
        games[0].last_played_version = "0.8";
        games[0].playtime_seconds += 300;
        localStorage.setItem("wlib-mock-games", JSON.stringify(games));
        window.dispatchEvent(new CustomEvent("wlib-playtime-tick", { detail: { gameId: 71, delta: 300, isFinal: false, lastPlayedVersion: "0.8", sessionId: 72 } }));
      });
      await panel().getByText("In progress", { exact: true }).waitFor();
      check(await badges().count() === 0, "The tick clears Unplayed live");
      for (const width of [375, 1280]) {
        await page.setViewportSize({ width, height: 900 });
        check(await panel().evaluate(el => el.scrollWidth <= el.clientWidth + 1), "Journal must fit narrow windows");
        await dialog.screenshot({ path: `/tmp/wlib-journal-${theme}-${width}.png` });
      }
      await dialog.getByRole("button", { name: "Stop", exact: true }).click();
      await panel().getByText("In progress", { exact: true }).waitFor({ state: "hidden" });
      await close();
      await page.getByRole("group", { name: "Never played fixture. Press Enter for details.", exact: true }).press("Enter");
      await dialog.getByRole("tab", { name: "Journal", exact: true }).click();
      await panel().getByText("No sessions have been recorded yet.", { exact: false }).waitFor();
      await page.evaluate(() => {
        window.pywebview = { api: { get_game_play_sessions: () => new Promise(resolve => { window.journalFixtureResolve = resolve; }) } };
      });
      await dialog.getByRole("tab", { name: "Overview", exact: true }).click();
      await dialog.getByRole("tab", { name: "Journal", exact: true }).click();
      await panel().getByRole("status").waitFor();
      await page.evaluate(() => {
        window.journalFixtureResolve({ success: true, sessions: [] });
        delete window.journalFixtureResolve;
        delete window.pywebview;
      });
      await panel().getByText("No sessions have been recorded yet.", { exact: false }).waitFor();
      await page.evaluate(() => { window.pywebview = { api: { get_game_play_sessions: () => ({ success: false, error: "Fixture error" }) } }; });
      await dialog.getByRole("tab", { name: "Overview", exact: true }).click();
      await dialog.getByRole("tab", { name: "Journal", exact: true }).click();
      await panel().getByRole("alert").waitFor();
      await page.evaluate(() => { delete window.pywebview; });
      await panel().getByRole("button", { name: "Retry", exact: true }).click();
      await panel().getByText("No sessions have been recorded yet.", { exact: false }).waitFor();
      await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
      check(await panel().evaluate(el => el.scrollWidth <= el.clientWidth + 1), "Journal supports 200% zoom");
      await page.evaluate(() => { document.documentElement.style.zoom = ""; });
      await close();
      await page.getByRole("link", { name: /^Updates,/ }).click();
      const toggle = page.getByRole("checkbox", { name: "Desktop notifications", exact: true });
      await toggle.waitFor();
      await toggle.uncheck();
      await page.reload();
      check(!await toggle.isChecked(), "Notification choice persists");
      await toggle.check();
      await page.evaluate(() => localStorage.setItem("wlib-mock-platform", "windows"));
      await page.reload();
      check(await toggle.count() === 0, "Windows hides desktop notifications");
      evidence.push(`${theme}: three layouts, AA text contrast, collection persistence, menu/modal skip and unskip, counts, live Journal and badge, 375/1280px, empty/error/retry, Linux toggle persistence, Windows hiding`);
    }
    check(errors.length === 0, `Unexpected browser errors: ${errors.join("; ")}`);
    return { status: "PASS", evidence };
  } finally {
    page.off("pageerror", onError);
    await page.evaluate(previous => {
      delete window.pywebview;
      delete window.journalFixtureResolve;
      document.documentElement.style.zoom = "";
      for (const [key, value] of previous) {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
      }
    }, previous);
    await page.goto(previousUrl);
    await page.reload();
    if (viewport) await page.setViewportSize(viewport);
  }
}
