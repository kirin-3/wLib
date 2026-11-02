// SPDX-License-Identifier: GPL-3.0-or-later
// Run against an isolated Vite preview: playwright-cli run-code --filename tests/performance_tools_ui_check.js
async (page) => {
  await page.addInitScript(() => { window.confirm = () => true; });
  const check = (value, message) => { if (!value) throw new Error(message); };
  const origin = new URL(page.url()).origin;
  const game = {
    id: 1, title: "Performance test", exe_path: "/games/example/game.exe",
    engine: "Unity", launch_mode: "wine_proton", custom_prefix: "/tmp/example-prefix",
    proton_version: "/tmp/example-proton", gamemode: "default", mangohud: "off",
  };
  const initialize = async (platform) => {
    await page.goto(`${origin}/#/`);
    await page.evaluate(({ platform, game }) => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-platform", platform);
      localStorage.setItem("wlib-mock-games", JSON.stringify([game]));
      localStorage.setItem("wlib-mock-settings", JSON.stringify({ gamemode_enabled: true, mangohud_enabled: false }));
    }, { platform, game });
    await page.reload();
  };
  await initialize("linux");
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Winecfg" }).waitFor();
  check(await page.getByRole("switch", { name: "GameMode" }).isDisabled(), "Missing GameMode is not disabled");
  check(await page.getByRole("switch", { name: "MangoHud" }).isDisabled(), "Missing MangoHud is not disabled");
  await page.getByRole("link", { name: "Library", exact: true }).click();
  await page.evaluate(async () => {
    const { api } = await import("/src/services/api.ts");
    window.__performanceCalls = [];
    api.getPerformanceTools = async () => ({ gamemode: true, mangohud: true });
    api.openWinePrefixTool = async (...args) => { window.__performanceCalls.push(["prefix", ...args]); return { success: true }; };
    const save = api.saveSettings.bind(api);
    api.saveSettings = async (payload) => { window.__performanceCalls.push(["settings", payload]); return save(payload); };
    const update = api.updateGame.bind(api);
    api.updateGame = async (...args) => { window.__performanceCalls.push(["game", ...args]); return update(...args); };
  });
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  const gmSwitch = page.getByRole("switch", { name: "GameMode" });
  await page.waitForFunction(() => { const el = document.querySelector('input[aria-label="GameMode"]'); return el && !el.disabled; });
  await page.locator("#settings-prefix-path").fill("/tmp/unsaved-prefix");
  await page.locator("#settings-proton-path").fill("/tmp/unsaved-runner");
  await page.getByRole("button", { name: "Winecfg" }).click();
  await page.getByRole("button", { name: "Winetricks" }).click();
  await gmSwitch.locator("..").click();
  await page.getByRole("switch", { name: "MangoHud" }).locator("..").click();
  const settingsCalls = await page.evaluate(() => window.__performanceCalls);
  check(settingsCalls.some((c) => c[0] === "prefix" && c[1] === "winecfg" && c[2] === "/tmp/unsaved-prefix" && c[3] === "/tmp/unsaved-runner"), "Settings ignored unsaved prefix or runner");
  check(settingsCalls.some((c) => c[0] === "prefix" && c[1] === "winetricks" && c[2] === "/tmp/unsaved-prefix" && c[3] === "/tmp/unsaved-runner"), "Winetricks ignored unsaved prefix or runner");
  check(settingsCalls.some((c) => c[0] === "settings" && c[1].gamemode_enabled === false), "Settings did not save the GameMode switch");
  check(settingsCalls.some((c) => c[0] === "settings" && c[1].mangohud_enabled === true), "Settings did not save the MangoHud switch");

  // Reload discards the deliberately unsaved Settings form.
  await page.goto(`${origin}/#/`);
  await page.reload();
  await page.evaluate(async () => {
    const { api } = await import("/src/services/api.ts");
    window.__performanceCalls = [];
    api.getPerformanceTools = async () => ({ gamemode: true, mangohud: true });
    api.openWinePrefixTool = async (...args) => { window.__performanceCalls.push(["prefix", ...args]); return { success: true }; };
    const update = api.updateGame.bind(api);
    api.updateGame = async (...args) => { window.__performanceCalls.push(["game", ...args]); return update(...args); };
  });
  await page.waitForTimeout(500);
  await page.locator(".game-card", { hasText: "Performance test" }).first().click();
  await page.getByRole("tab", { name: "Launch" }).click();
  const gamemode = page.getByRole("combobox", { name: "GameMode" });
  const mangohud = page.getByRole("combobox", { name: "MangoHud" });
  await page.waitForFunction(() => { const el = document.querySelector("#detail-gamemode"); return el && !el.disabled; });
  check(await gamemode.locator("option").allTextContents().then((options) => options.join(",") === "Default (Off),On,Off"), "GameMode options do not show the current global default");
  check(await mangohud.locator("option").allTextContents().then((options) => options.join(",") === "Default (On),On,Off"), "MangoHud options do not show the current global default");
  await gamemode.selectOption("on");
  await page.getByRole("switch", { name: "Run in sandbox" }).check();
  check(await gamemode.isDisabled(), "Sandbox did not disable GameMode");
  check(await mangohud.isEnabled(), "Sandbox disabled MangoHud");
  await page.getByRole("button", { name: "Winecfg" }).click();
  await page.getByRole("button", { name: "Winetricks" }).click();
  await page.getByRole("checkbox", { name: "Use Custom Wine Prefix" }).locator("..").click();
  check(await page.getByRole("button", { name: "Winecfg" }).count() === 0, "Prefix tools visible without a custom prefix");
  await page.getByRole("checkbox", { name: "Use Custom Wine Prefix" }).locator("..").click();
  await page.getByRole("button", { name: "Save Changes" }).click();
  const gameCalls = await page.evaluate(() => window.__performanceCalls);
  check(gameCalls.some((c) => c[0] === "prefix" && c[1] === "winecfg" && c[2] === game.custom_prefix && c[3] === game.proton_version), "Game prefix tool used the wrong target");
  check(gameCalls.some((c) => c[0] === "prefix" && c[1] === "winetricks" && c[2] === game.custom_prefix && c[3] === game.proton_version), "Game Winetricks used the wrong target");
  check(gameCalls.some((c) => c[0] === "game" && c[2].gamemode === "on" && c[2].mangohud === "off"), "Game save lost the performance settings");

  await initialize("windows");
  await page.evaluate(async () => {
    const { api } = await import("/src/services/api.ts");
    const original = api.getPlatformCapabilities.bind(api);
    api.getPlatformCapabilities = async () => ({ ...(await original()), platform: "windows" });
  });
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByText("Windows games launch directly.", { exact: false }).waitFor();
  check(await page.getByRole("button", { name: "Winecfg" }).count() === 0, "Settings exposed Winecfg on Windows");
  check(await page.getByRole("switch", { name: "GameMode" }).count() === 0, "Settings exposed GameMode on Windows");
  await page.getByRole("link", { name: "Library", exact: true }).click();
  await page.waitForTimeout(500);
  await page.locator(".game-card", { hasText: "Performance test" }).first().click();
  await page.getByRole("tab", { name: "Launch" }).click();
  check(await page.getByRole("combobox", { name: "GameMode" }).count() === 0, "Game exposed GameMode on Windows");
  check(await page.getByRole("button", { name: "Winecfg" }).count() === 0, "Game exposed Winecfg on Windows");
  return "Performance tool component checks passed";
}
