// SPDX-License-Identifier: GPL-3.0-or-later
// With the Vite app open in an isolated playwright-cli browser session:
// playwright-cli run-code --filename tests/game_detail_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-platform", "wlib-mock-launch-targets", "wlib-library-view-state", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      const targets = [
        { id: 1, game_id: 1, label: "Part 2", exe_path: "/games/aurora/part2.exe", sort_order: 0 },
        { id: 2, game_id: 1, label: "Extras", exe_path: "/games/aurora/extras.exe", sort_order: 1 },
      ];
      const cover = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400"><rect width="300" height="400" fill="#1e3a5f"/><circle cx="150" cy="150" r="85" fill="#83c9de"/><path d="M0 400V270L90 180L190 320L300 230V400" fill="#274873"/></svg>');
      localStorage.setItem("wlib-mock-games", JSON.stringify([
        { id: 1, title: "Aurora", developer: "Example Studio", exe_path: "/games/aurora/game.exe", f95_url: "https://f95zone.to/threads/123/", cover_image_path: cover, version: "1.0", latest_version: "2.0", engine: "Unity", tags: "Adventure, Fantasy", rating: "4.2", playtime_seconds: 7200, last_played: "2026-10-01", launch_targets: targets, custom_prefix: "/prefix/aurora", proton_version: "/runners/custom", launch_mode: "auto" },
        { id: 2, title: "No Cover", exe_path: "/games/other/game.exe", latest_version: "3.0" },
      ]));
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-mock-launch-targets", JSON.stringify(targets));
      localStorage.setItem("wlib-theme", "dark");
      localStorage.removeItem("wlib-library-view-state");
    });
    await page.goto(new URL("/#/", previousUrl).toString());
    await page.reload();
    const open = title => page.getByRole("group", { name: `${title}. Press Enter for details.`, exact: true }).press("Enter");
    await open("Aurora");
    const dialog = page.getByRole("dialog");
    const save = dialog.getByRole("button", { name: "Save Changes", exact: true });
    const tab = name => dialog.getByRole("tab", { name, exact: true });
    const dismissError = async message => {
      const alert = page.getByRole("alert").filter({ hasText: message });
      await alert.getByRole("button", { name: "Dismiss notification", exact: true }).click();
      await alert.waitFor({ state: "hidden" });
    };
    await dialog.getByRole("heading", { name: "Aurora", exact: true }).waitFor();
    check(await dialog.getByRole("img", { name: "Aurora cover" }).isVisible(), "The header must show the cover");
    check(await dialog.getByText("by Example Studio").isVisible(), "The header must show the developer");
    check(await dialog.getByText("Update: 2.0").isVisible(), "The header must show available updates");
    check(await tab("Overview").getAttribute("aria-selected") === "true", "Overview must open by default");
    check(await dialog.locator(".summary-card").count() === 4, "Timestamps must not occupy stat tiles");
    check(await dialog.getByRole("button", { name: "Remove tag Fantasy" }).isVisible(), "Tags must be expanded by default");
    check(await save.count() === 0, "Save must be hidden without edits");
    check(!await dialog.getByLabel("Executable Path", { exact: true }).isVisible(), "Overview must hide launch settings");

    await dialog.getByRole("button", { name: "Rate Graphics 4 of 5" }).click();
    await dialog.getByLabel("New tag").fill("Test tag");
    await dialog.getByLabel("New tag").press("Enter");
    await page.waitForFunction(() => JSON.parse(localStorage.getItem("wlib-mock-games"))[0].tags.includes("Test tag"));
    check(await save.count() === 0, "Immediate ratings and tags must not reveal Save");

    await page.evaluate(() => {
      window.pywebview = { api: { update_game: () => ({ success: false, error: "Simulated failure" }) } };
    });
    await dialog.getByRole("button", { name: "Rate Graphics 5 of 5" }).click();
    await page.getByText("Failed to save: Simulated failure", { exact: true }).waitFor();
    check(await save.count() === 1, "Failed immediate saves must reveal Save for retry");
    await dismissError("Failed to save: Simulated failure");
    await save.click();
    await page.getByText("Failed to save game: Simulated failure", { exact: true }).waitFor();
    check(await dialog.isVisible() && await save.isVisible(), "A failed explicit save must retain edits for retry");
    await dismissError("Failed to save game: Simulated failure");
    await page.evaluate(() => {
      window.gameDetailOriginalConfirm = window.confirm;
      window.confirm = message => { window.gameDetailConfirmation = message; return false; };
    });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    check(await page.evaluate(() => window.gameDetailConfirmation === "Discard unsaved changes to this game?"), "Closing failed edits must request confirmation");
    check(await dialog.isVisible(), "Failed immediate saves must be protected from accidental close");
    await page.evaluate(() => {
      window.confirm = window.gameDetailOriginalConfirm;
      delete window.gameDetailOriginalConfirm;
      delete window.gameDetailConfirmation;
      delete window.pywebview;
    });
    await dialog.getByRole("button", { name: "Mark Favorite", exact: true }).click();
    check(await save.count() === 1, "Saving another quick field must not clear a failed edit");
    await save.click();
    await dialog.waitFor({ state: "hidden" });
    check(await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-games"))[0].rating_graphics === 5), "Retrying must persist the failed rating");
    await open("Aurora");
    check(await save.count() === 0, "Retrying a failed immediate save must clear pending edits");

    await tab("Overview").focus();
    await page.keyboard.press("ArrowRight");
    check(await tab("Launch").getAttribute("aria-selected") === "true", "ArrowRight must select Launch");
    check(await tab("Launch").evaluate(el => el === document.activeElement), "Keyboard tab changes must move focus");
    await page.keyboard.press("End");
    check(await tab("Info").getAttribute("aria-selected") === "true", "End must select the last tab");
    await page.keyboard.press("Home");
    check(await tab("Overview").getAttribute("aria-selected") === "true", "Home must select the first tab");
    await page.keyboard.press("ArrowLeft");
    check(await tab("Info").getAttribute("aria-selected") === "true", "ArrowLeft must wrap to the last tab");
    await tab("Launch").click();
    await dialog.getByLabel("Runtime Mode").waitFor();
    await dialog.getByText("Enable this if the game has unreadable text", { exact: false }).waitFor();
    check(await dialog.getByRole("button", { name: "Move Part 2 up" }).isDisabled(), "The first target cannot move up");
    await dialog.getByRole("button", { name: "Move Extras up" }).click();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem("wlib-mock-launch-targets")).find(target => target.label === "Extras").sort_order === 0);
    await dialog.getByLabel("Command Line Arguments").fill("--fullscreen");
    await save.waitFor();
    await tab("Info").click();
    check(await dialog.getByLabel("Title", { exact: true }).inputValue() === "Aurora", "Info must contain title editing");
    check(!await dialog.getByLabel("Runtime Mode").isVisible(), "Info must hide launch settings");
    await dialog.getByLabel("Title", { exact: true }).fill("Aurora edited");
    await tab("Launch").click();
    check(await dialog.getByLabel("Command Line Arguments").inputValue() === "--fullscreen", "Edits must survive tab switches");
    await dialog.getByLabel("Command Line Arguments").fill("");
    await tab("Info").click();
    await dialog.getByLabel("Title", { exact: true }).fill("Aurora");
    check(await save.count() === 0, "Reverting all edits must hide Save");

    await dialog.locator("header").getByRole("button", { name: "Play", exact: true }).click();
    await dialog.locator("header").getByRole("button", { name: "Stop", exact: true }).waitFor();
    await tab("Launch").click();
    check(await dialog.locator(".launch-target-panel").getByRole("button", { name: "Play", exact: true }).count() === 0, "Target Play actions must hide while running");
    await dialog.locator("header").getByRole("button", { name: "Stop", exact: true }).click();
    await dialog.locator("header").getByRole("button", { name: "Play", exact: true }).waitFor();
    await tab("Info").click();
    await dialog.getByLabel("Version", { exact: true }).fill("2.0");
    await save.click();
    await dialog.waitFor({ state: "hidden" });
    check(await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-games"))[0].version === "2.0"), "Save must persist edits");
    await open("Aurora");
    check(await tab("Overview").getAttribute("aria-selected") === "true", "Reopening must reset the selected tab");
    check(await save.count() === 0, "Reopening a saved game must hide Save");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await open("No Cover");
    check(await dialog.getByRole("img", { name: "No cover image" }).isVisible(), "Missing covers must have a fallback");
    check(await dialog.getByText("Update: 3.0").isVisible(), "Updates must be shown when the installed version is unknown");
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(() => document.documentElement.classList.add("light"));
    for (const name of ["Overview", "Launch", "Info"]) {
      await tab(name).click();
      const fits = await dialog.getByRole("tabpanel", { name }).evaluate(panel => [...panel.querySelectorAll("div")].every(el => el.scrollWidth <= el.clientWidth + 2));
      check(fits, `${name} must fit a narrow window`);
    }
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    return "PASS: header and update badges, keyboard tabs, autosave failure recovery, edit tracking, target ordering, Play/Stop, saving and narrow layouts";
  } finally {
    await page.evaluate(previous => {
      delete window.pywebview;
      for (const [key, value] of previous) {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
      }
    }, previous);
    await page.goto(previousUrl);
    await page.reload();
    if (previousViewport) await page.setViewportSize(previousViewport);
  }
}
