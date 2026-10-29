// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/game_detail_theme_contrast_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const measure = async selectors => page.evaluate(selectors => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const rgba = color => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
      return [red, green, blue, alpha / 255];
    };
    const composite = (top, bottom) => {
      const alpha = top[3] + bottom[3] * (1 - top[3]);
      if (alpha === 0) return [255, 255, 255, 1];
      return [
        (top[0] * top[3] + bottom[0] * bottom[3] * (1 - top[3])) / alpha,
        (top[1] * top[3] + bottom[1] * bottom[3] * (1 - top[3])) / alpha,
        (top[2] * top[3] + bottom[2] * bottom[3] * (1 - top[3])) / alpha,
        alpha,
      ];
    };
    const background = element => {
      const layers = [];
      let node = element;
      while (node) {
        const color = rgba(getComputedStyle(node).backgroundColor);
        if (color[3] > 0) layers.push(color);
        node = node.parentElement;
      }
      let result = [255, 255, 255, 1];
      for (const layer of layers.reverse()) result = composite(layer, result);
      return result;
    };
    const luminance = color => color.slice(0, 3)
      .map(channel => channel / 255)
      .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
      .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
    const results = [];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (!element.getClientRects().length || element.disabled) continue;
        const foreground = luminance(rgba(getComputedStyle(element).color));
        const behind = luminance(background(element));
        const ratio = (Math.max(foreground, behind) + 0.05) / (Math.min(foreground, behind) + 0.05);
        results.push({ selector, text: element.textContent.trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100 });
      }
    }
    return results;
  }, selectors);
  const openDetail = async () => {
    const card = page.locator(".game-card", { hasText: "Aurora" }).first();
    await card.waitFor({ timeout: 5000 });
    await card.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("heading", { name: "Aurora", exact: true }).waitFor();
    return dialog;
  };
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
          latest_version: "2.0",
          rating: "4.2",
          rating_graphics: 4,
          rating_story: 4,
          rating_fappability: 4,
          rating_gameplay: 4,
          engine: "Unity",
        },
      ]));
      localStorage.setItem("wlib-mock-platform", "linux");
    });
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();

    const failures = [];
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => localStorage.setItem("wlib-theme", theme), theme);
      await page.reload();
      const dialog = await openDetail();
      check(
        await dialog.locator(".update-badge").isVisible(),
        `The update badge must be visible in ${theme} theme`,
      );
      for (const result of await measure([".summary-value", ".update-badge"])) {
        if (result.ratio < 4.5) failures.push({ theme, ...result });
      }
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      await dialog.waitFor({ state: "hidden" });
    }

    check(failures.length === 0, "Game detail text below 4.5:1 contrast: " + JSON.stringify(failures));
    return "PASS: game detail summary ratings and update badge meet 4.5:1 contrast in both themes";
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
