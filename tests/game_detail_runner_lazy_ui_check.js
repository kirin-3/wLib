// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/game_detail_runner_lazy_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-mock-launch-targets", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", JSON.stringify([
        {
          id: 1,
          title: "Aurora",
          exe_path: "/games/aurora/game.exe",
          version: "1.0",
          engine: "Unity",
          launch_mode: "auto",
        },
      ]));
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-theme", "dark");
      localStorage.removeItem("wlib-mock-launch-targets");
    });
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();

    const card = page.locator(".game-card", { hasText: "Aurora" }).first();
    await card.waitFor({ timeout: 5000 });
    await page.evaluate(() => {
      window.__runnerCalls = 0;
      window.pywebview = {
        api: {
          get_available_runners: () => {
            window.__runnerCalls++;
            return Promise.resolve({
              success: true,
              runners: [
                { name: "System Wine", path: "wine" },
                { name: "GE-Proton", path: "/runners/GE-Proton/proton" },
              ],
            });
          },
        },
      };
    });
    await card.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("heading", { name: "Aurora", exact: true }).waitFor();

    // 1. Opening the modal and visiting Launch must not fetch the runner list.
    await page.waitForTimeout(400);
    check(
      await page.evaluate(() => window.__runnerCalls) === 0,
      "Opening the game detail modal must not call getAvailableRunners",
    );
    await dialog.getByRole("tab", { name: "Launch", exact: true }).click();
    await dialog.getByLabel("Runtime Mode").waitFor();
    await page.waitForTimeout(400);
    check(
      await page.evaluate(() => window.__runnerCalls) === 0,
      "Visiting the Launch tab must not call getAvailableRunners",
    );
    check(
      await dialog.getByLabel("Runner (Wine / Proton)").isVisible(),
      "The runner list field must be present in the Advanced Launch Options",
    );

    // 2. Enabling the Custom Prefix toggle loads the list once.
    const customPrefix = dialog.getByLabel("Use Custom Wine Prefix", { exact: true });
    await customPrefix.evaluate(element => element.click());
    await page.waitForFunction(() => window.__runnerCalls === 1);
    await dialog
      .getByLabel("Runner (Wine / Proton)")
      .locator("option", { hasText: "System Wine" })
      .waitFor({ state: "attached" });

    // 3. Disabling and re-enabling the toggle reuses the loaded list.
    await customPrefix.evaluate(element => element.click());
    await customPrefix.evaluate(element => element.click());
    await customPrefix.evaluate(element => element.click());
    await page.waitForTimeout(300);
    check(
      await page.evaluate(() => window.__runnerCalls) === 1,
      "Re-enabling the Custom Prefix toggle must not fetch the runner list again",
    );

    // 4. A stored custom prefix does not fetch the list just by opening.
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    await page.evaluate(() => {
      const games = JSON.parse(localStorage.getItem("wlib-mock-games"));
      games[0].custom_prefix = "/prefix/aurora";
      localStorage.setItem("wlib-mock-games", JSON.stringify(games));
    });
    await page.reload();
    const reloadedCard = page.locator(".game-card", { hasText: "Aurora" }).first();
    await reloadedCard.waitFor({ timeout: 5000 });
    await page.evaluate(() => {
      window.__runnerCalls = 0;
      window.pywebview = {
        api: {
          get_available_runners: () => {
            window.__runnerCalls++;
            return Promise.resolve({ success: true, runners: [{ name: "System Wine", path: "wine" }] });
          },
        },
      };
    });
    await reloadedCard.click();
    await dialog.getByRole("heading", { name: "Aurora", exact: true }).waitFor();
    await dialog.getByRole("tab", { name: "Launch", exact: true }).click();
    await dialog.getByLabel("Runtime Mode").waitFor();
    await page.waitForTimeout(400);
    check(
      await page.evaluate(() => window.__runnerCalls) === 0,
      "A stored custom prefix must not fetch the runner list on open",
    );

    return "PASS: the runner list loads only when the user opens Custom Prefix, and is reused afterwards";
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      for (const [key, value] of previous) {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
      }
    }, previous);
    if (previousViewport) await page.setViewportSize(previousViewport);
    await page.goto(previousUrl);
    await page.reload();
  }
}
