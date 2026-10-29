// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/game_detail_modal_parallel_ui_check.js
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
          launch_mode: "auto",
        },
      ]));
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-theme", "dark");
    });
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();

    const card = page.locator(".game-card", { hasText: "Aurora" }).first();
    await card.waitFor({ timeout: 5000 });
    await page.evaluate(() => {
      const t0 = performance.now();
      window.__modalCalls = [];
      window.__platformResolved = false;
      const at = () => Math.round(performance.now() - t0);
      window.pywebview = {
        api: {
          get_platform_capabilities: () => {
            window.__modalCalls.push({ name: "platform", at: at() });
            return new Promise(resolve => {
              setTimeout(() => {
                window.__platformResolved = true;
                window.__modalCalls.push({ name: "platform resolved", at: at() });
                resolve({
                  success: true,
                  platform: "linux",
                  wine_proton: true,
                  wayland: true,
                  cheat_engine_injection: true,
                  rpgmaker_linux: false,
                  companion_tools: false,
                  runtime_installers: false,
                  launch_modes: ["auto", "native", "wine_proton", "custom"],
                  extension_dir: "",
                });
              }, 1500);
            });
          },
          is_cheat_engine_installed: () => {
            window.__modalCalls.push({ name: "cheat engine", at: at(), platformResolved: window.__platformResolved });
            return Promise.resolve({ installed: true, path: "/cheat-engine" });
          },
        },
      };
    });
    await card.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("heading", { name: "Aurora", exact: true }).waitFor();

    // The Cheat Engine check must not wait for the platform capability read.
    await page.waitForFunction(
      () => (window.__modalCalls || []).some(call => call.name === "cheat engine"),
      null,
      { timeout: 800 },
    );
    const call = await page.evaluate(() => window.__modalCalls.find(entry => entry.name === "cheat engine"));
    check(call.platformResolved === false, "isCheatEngineInstalled must not wait for getPlatformCapabilities");

    // The modal still settles normally once the capability read returns.
    await page.waitForFunction(() => window.__platformResolved, null, { timeout: 3000 });
    await dialog.getByRole("tab", { name: "Launch", exact: true }).click();
    await dialog.getByLabel("Runtime Mode").waitFor({ timeout: 3000 });
    return "PASS: the Cheat Engine check starts on modal open without waiting for platform capabilities";
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
