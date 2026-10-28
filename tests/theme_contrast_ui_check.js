// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/theme_contrast_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  const failures = [];
  const measure = async (theme, selectors) => {
    const measured = await page.evaluate(selectors => {
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
          results.push({ selector, text: element.textContent.trim().slice(0, 60), ratio: Math.round(ratio * 100) / 100 });
        }
      }
      return results;
    }, selectors);
    for (const result of measured) {
      if (result.ratio < 4.5) failures.push({ theme, ...result });
    }
  };

  try {
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-mock-games", "[]");
      localStorage.setItem("wlib-mock-settings", "{}");
      localStorage.setItem("wlib-theme", "light");
    });
    const url = new URL(previousUrl); url.hash = "/extension";
    await page.goto(url.toString());
    await page.reload();
    await page.getByRole("heading", { name: "Browser Extension", exact: true }).waitFor();
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => document.documentElement.classList.toggle("light", theme === "light"), theme);
      await measure(theme, [".ext-card span", ".ext-card code"]);
    }

    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
    await page.getByRole("heading", { name: "RPGMaker Linux Runner", exact: true }).waitFor();
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => document.documentElement.classList.toggle("light", theme === "light"), theme);
      await measure(theme, [".setting-row p.copyable-feedback"]);
    }
    check(failures.length === 0, "Text below 4.5:1 contrast: " + JSON.stringify(failures));
    return "PASS: extension badges/code and Settings status text meet 4.5:1 in both themes";
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
