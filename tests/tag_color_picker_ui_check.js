// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/tag_color_picker_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-theme"];
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
          tags: "Adventure, Fantasy",
        },
      ]));
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-theme", "dark");
      localStorage.removeItem("wlib-mock-settings");
    });
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();

    const card = page.locator(".game-card", { hasText: "Aurora" }).first();
    await card.waitFor({ timeout: 5000 });
    await card.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("heading", { name: "Aurora", exact: true }).waitFor();
    const menu = page.getByRole("menu");
    const detailTag = dialog.locator(".tag-pill", { hasText: "Adventure" }).first();

    // 1. A click on the picker's own label leaves focus on the document. Escape
    // must still close the picker and leave the game modal open.
    await detailTag.click({ button: "right" });
    await menu.waitFor();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "hidden" });
    check(await dialog.isVisible(), "Escape from the tag color picker must leave the game detail modal open");

    // 2. The same focus state must not let Tab reach the modal's focus trap.
    await detailTag.click({ button: "right" });
    await menu.waitFor();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Tab");
    await menu.waitFor({ state: "hidden" });
    check(await dialog.isVisible(), "Tab from the tag color picker must leave the game detail modal open");

    // 3. Escape with focus on a swatch continues to close only the picker.
    await detailTag.click({ button: "right" });
    await menu.waitFor();
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "hidden" });
    check(await dialog.isVisible(), "Escape on a focused swatch must leave the game detail modal open");

    // 4. Choosing a swatch still applies and saves it.
    await detailTag.click({ button: "right" });
    await menu.waitFor();
    await menu.getByRole("menuitemradio", { name: "Green", exact: true }).click();
    await menu.waitFor({ state: "hidden" });
    check(
      await detailTag.getAttribute("data-tag-color") === "green",
      "Choosing Green must color the tag in the game detail modal",
    );
    await page.waitForFunction(() => {
      const settings = JSON.parse(localStorage.getItem("wlib-mock-settings") || "{}");
      return settings.tag_colors?.adventure === "green";
    });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });

    // 5. The library tag filter picker gets the same Escape handling.
    const filterTag = page.locator(".tag-pill", { hasText: "Adventure" }).first();
    await filterTag.waitFor();
    await filterTag.dispatchEvent("contextmenu");
    await menu.waitFor();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "hidden" });

    // 6. Escape with focus on a swatch also closes the library picker.
    await filterTag.dispatchEvent("contextmenu");
    await menu.waitFor();
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "hidden" });

    return "PASS: the tag color picker closes on Escape and Tab from any focus state without closing the game detail modal";
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
