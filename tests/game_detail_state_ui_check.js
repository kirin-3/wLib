// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/game_detail_state_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = [
    "wlib-mock-games",
    "wlib-mock-settings",
    "wlib-mock-platform",
    "wlib-mock-launch-targets",
    "wlib-mock-attachments",
    "wlib-mock-play-sessions",
    "wlib-theme",
  ];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.setItem("wlib-mock-games", JSON.stringify([
        { id: 1, title: "Aurora", developer: "Dev", exe_path: "/games/aurora/game.exe", f95_url: "https://f95zone.to/threads/1/", version: "1.0", engine: "Unity", tags: "A", progress: "" },
        { id: 2, title: "Vista", exe_path: "/games/vista/game.exe", version: "1.0", engine: "Ren'Py" },
      ]));
      const settings = JSON.parse(localStorage.getItem("wlib-mock-settings") || "{}");
      settings.collections = ["Race Collection"];
      settings.custom_play_statuses = [];
      localStorage.setItem("wlib-mock-settings", JSON.stringify(settings));
      localStorage.setItem("wlib-theme", "dark");
    });
    const url = new URL(previousUrl); url.hash = "/";
    await page.goto(url.toString());
    await page.reload();

    const card = () => page.getByRole("group", { name: /^Aurora(?: .*)?\. Press Enter for details\.$/ });
    const openAurora = async () => {
      const locator = card();
      await locator.waitFor({ timeout: 5000 }).catch(async () => {
        const state = await page.evaluate(() => ({ url: location.href, text: document.body.innerText.slice(-400) }));
        throw new Error("Aurora card did not appear: " + JSON.stringify(state));
      });
      await locator.press("Enter");
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("heading", { name: /^Aurora/ }).waitFor();
      return dialog;
    };

    // 1. An explicit save must not absorb edits typed while it is in flight.
    let dialog = await openAurora();
    const tab = name => dialog.getByRole("tab", { name, exact: true });
    const save = () => dialog.getByRole("button", { name: "Save Changes", exact: true });
    await tab("Info").click();
    const title = () => dialog.getByLabel("Title", { exact: true });
    await title().fill("Aurora first edit");
    await page.evaluate(() => {
      window.pywebview = {
        api: {
          update_game: (id, fields) => new Promise(resolve => {
            setTimeout(() => {
              const games = JSON.parse(localStorage.getItem("wlib-mock-games") || "[]");
              const game = games.find(entry => entry.id === Number(id));
              if (game) Object.assign(game, fields);
              localStorage.setItem("wlib-mock-games", JSON.stringify(games));
              resolve({ success: true, mock: true });
            }, 900);
          }),
        },
      };
    });
    await save().click();
    await page.waitForFunction(() => document.querySelector(".save-btn")?.textContent.includes("Saving"));
    await title().fill("Aurora second edit");
    await page.waitForFunction(() => {
      const stored = JSON.parse(localStorage.getItem("wlib-mock-games") || "[]");
      return stored.find(game => game.id === 1)?.title === "Aurora first edit";
    });
    await page.waitForFunction(() => {
      const button = document.querySelector(".save-btn");
      return !!button && button.textContent.includes("Save Changes");
    });
    check(await dialog.isVisible(), "A save that finishes while the form changed must not close the modal");
    check(await save().isVisible(), "Edits made during the save must stay available to save");
    check(
      await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-games")).find(game => game.id === 1).title) === "Aurora first edit",
      "The submitted title must be the one stored by the first save",
    );
    check(await title().inputValue() === "Aurora second edit", "The newer title must stay in the form");
    await page.evaluate(() => { delete window.pywebview; });
    await save().click();
    await dialog.waitFor({ state: "hidden" });
    check(
      await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-games")).find(game => game.id === 1).title) === "Aurora second edit",
      "The retry must persist the newer title",
    );

    // 2. Leaving for Settings from inside the modal must ask before discarding edits.
    dialog = await openAurora();
    const secondTab = name => dialog.getByRole("tab", { name, exact: true });
    const secondTitle = () => dialog.getByLabel("Title", { exact: true });
    await secondTab("Info").click();
    const savedTitle = await secondTitle().inputValue();
    await secondTitle().fill("Aurora unsaved link");
    await page.evaluate(() => {
      window.__confirms = [];
      window.__originalConfirm = window.confirm;
      window.confirm = message => { window.__confirms.push(message); return false; };
    });
    await secondTab("Launch").click();
    await dialog.getByRole("button", { name: "Open Sandbox settings", exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Open Sandbox settings", exact: true }).click();
    await page.waitForFunction(() => (window.__confirms || []).length > 0);
    check(
      await page.evaluate(() => window.__confirms.includes("Discard unsaved changes to this game?")),
      "Opening Sandbox settings from an edited game must ask before discarding",
    );
    check(await dialog.isVisible(), "Declining the discard prompt must keep the game modal open");
    check(!page.url().includes("/settings"), "Declining the discard prompt must not navigate away");
    await page.evaluate(() => {
      window.confirm = window.__originalConfirm;
      delete window.__originalConfirm;
      delete window.__confirms;
    });
    await secondTab("Info").click();
    await secondTitle().fill(savedTitle);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });

    // 3. A failed collection save must keep the modal open for a retry.
    await page.evaluate(() => {
      window.pywebview = {
        api: {
          get_platform_capabilities: () => ({
            success: true,
            platform: "linux",
            wine_proton: true,
            wayland: true,
            cheat_engine_injection: false,
            rpgmaker_linux: false,
            companion_tools: false,
            runtime_installers: false,
            launch_modes: ["auto", "native", "wine_proton", "custom"],
            extension_dir: "",
          }),
          update_game: (id, fields) => {
            const games = JSON.parse(localStorage.getItem("wlib-mock-games") || "[]");
            const game = games.find(entry => entry.id === Number(id));
            if (game) Object.assign(game, fields);
            localStorage.setItem("wlib-mock-games", JSON.stringify(games));
            return { success: true, mock: true };
          },
          set_game_collections: () => ({ success: false, mock: true, error: "Simulated collection failure" }),
        },
      };
    });
    dialog = await openAurora();
    const chip = dialog.getByRole("button", { name: "Race Collection", exact: true });
    await chip.waitFor();
    await chip.click();
    await dialog.getByRole("button", { name: "Save Changes", exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Save Changes", exact: true }).click();
    await page.getByText("Failed to save collections: Simulated collection failure", { exact: true }).waitFor();
    check(await dialog.isVisible(), "A failed collection save must keep the modal open");
    check(
      await dialog.getByRole("button", { name: "Save Changes", exact: true }).isVisible(),
      "A failed collection save must leave Save available for a retry",
    );
    check(await dialog.getByText("Unsaved changes", { exact: true }).isVisible(), "The failed collection change must stay unsaved");
    await page.evaluate(() => { delete window.pywebview; });
    await dialog.getByRole("button", { name: "Save Changes", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    check(
      await page.evaluate(() => {
        const game = JSON.parse(localStorage.getItem("wlib-mock-games")).find(entry => entry.id === 1);
        return (game.collections || []).includes("Race Collection");
      }),
      "Retrying must persist the collection membership",
    );

    // 4. Opening the modal must not fetch the runner list; focusing the runner
    // select is the first time it is needed.
    await page.evaluate(() => {
      window.__runnerCalls = 0;
      window.pywebview = {
        api: {
          get_platform_capabilities: () => ({
            success: true,
            platform: "linux",
            wine_proton: true,
            wayland: true,
            cheat_engine_injection: false,
            rpgmaker_linux: false,
            companion_tools: false,
            runtime_installers: false,
            launch_modes: ["auto", "native", "wine_proton", "custom"],
            extension_dir: "",
          }),
          get_available_runners: () => {
            window.__runnerCalls++;
            return Promise.resolve({ success: true, runners: [{ name: "System Wine", path: "wine" }] });
          },
        },
      };
    });
    dialog = await openAurora();
    await page.waitForTimeout(300);
    check(
      await page.evaluate(() => window.__runnerCalls) === 0,
      "Opening the game detail modal must not fetch the runner list",
    );
    await dialog.getByRole("tab", { name: "Launch", exact: true }).click();
    const runner = dialog.getByLabel("Runner (Wine / Proton)", { exact: true });
    await runner.focus();
    await page.waitForFunction(() => window.__runnerCalls === 1);
    await page.evaluate(() => document.activeElement?.blur());
    await runner.focus();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check(
      await page.evaluate(() => window.__runnerCalls) === 1,
      "Focusing the runner select again must reuse the loaded list",
    );
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });

    // A stored custom prefix must not count as opening the toggle either; only
    // the user's change loads the list.
    await page.evaluate(() => { window.__runnerCalls = 0; });
    dialog = await openAurora();
    await page.waitForTimeout(300);
    check(
      await page.evaluate(() => window.__runnerCalls) === 0,
      "A freshly opened modal must not fetch the runner list",
    );
    await dialog.getByRole("tab", { name: "Launch", exact: true }).click();
    await dialog.getByLabel("Use Custom Wine Prefix", { exact: true }).check({ force: true });
    await page.waitForFunction(() => window.__runnerCalls === 1);
    await dialog.getByLabel("Use Custom Wine Prefix", { exact: true }).uncheck({ force: true });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    return "PASS: save-in-flight edits stay unsaved, Settings links respect the discard prompt, failed collection saves stay retryable, runners load lazily";
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      if (window.__originalConfirm) window.confirm = window.__originalConfirm;
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
