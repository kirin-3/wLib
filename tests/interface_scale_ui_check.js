// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/interface_scale_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-interface-scale", "wlib-text-size", "wlib-mock-games", "wlib-library-view-state"];
  const previous = await page.evaluate(keys => Object.fromEntries(keys.map(key => [key, localStorage.getItem(key)])), keys);
  const withinViewport = async locator => locator.evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.left >= -1 && r.top >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1;
  });
  try {
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-interface-scale", "150");
      localStorage.setItem("wlib-text-size", "default");
      localStorage.setItem("wlib-library-view-state", JSON.stringify({ layoutMode: "grid" }));
      localStorage.setItem("wlib-mock-games", JSON.stringify(Array.from({ length: 8 }, (_, index) => ({ id: index + 1, title: index === 0 ? "Scale check" : `Z scale ${index}`, exe_path: "/games/scale/game.exe", version: "1.0", engine: "Unity" }))));
    });
    const url = new URL(previousUrl); url.hash = "/";
    await page.goto(url.toString());
    await page.reload();
    const card = page.getByRole("group", { name: "Scale check. Press Enter for details.", exact: true });
    await card.waitFor();
    check(await page.evaluate(() => {
      const el = document.scrollingElement;
      return el.scrollWidth <= innerWidth + 1 && el.scrollHeight <= innerHeight + 1;
    }), "Root must not overflow at 150% in a 1200×800 viewport");
    await card.click();
    const dialog = page.getByRole("dialog");
    await dialog.waitFor();
    check(await withinViewport(dialog), "Game Detail must fit the viewport at 150%");
    await page.keyboard.press("Escape");
    await card.evaluate(el => {
      const scroller = el.closest(".overflow-y-auto");
      scroller.scrollTop += (el.getBoundingClientRect().top - 100) / 1.5;
    });
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const cardBounds = await card.boundingBox();
    const point = { x: cardBounds.x + 10, y: cardBounds.y + 10 };
    await page.mouse.click(point.x, point.y, { button: "right" });
    const menu = page.locator(".game-context-menu");
    await menu.waitFor();
    const bounds = await menu.boundingBox();
    check(Math.abs(bounds.x - point.x) <= 3 && Math.abs(bounds.y - point.y) <= 3, "Context menu must open at the pointer: " + JSON.stringify({ bounds, point, cardBounds }));
    check(await withinViewport(menu), "Context menu must fit the viewport");
    await page.keyboard.press("Escape");
    return "PASS: 150% root overflow, Game Detail bounds, and context menu coordinates";
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      for (const [key, value] of Object.entries(previous)) {
        if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
      }
    }, previous);
    if (previousViewport) await page.setViewportSize(previousViewport);
    await page.goto(previousUrl);
    await page.reload();
  }
}
