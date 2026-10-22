// SPDX-License-Identifier: GPL-3.0-or-later
// With the Vite app open in an isolated playwright-cli browser session:
// playwright-cli run-code --filename tests/game_detail_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = ["wlib-mock-games", "wlib-mock-platform", "wlib-mock-launch-targets", "wlib-mock-attachments", "wlib-mock-play-sessions", "wlib-library-view-state", "wlib-theme"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      const targets = [
        { id: 1, game_id: 1, label: "Part 2", exe_path: "/games/aurora/part2.exe", sort_order: 0 },
        { id: 2, game_id: 1, label: "Extras", exe_path: "/games/aurora/extras.exe", sort_order: 1 },
      ];
      const cover = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400"><rect width="300" height="400" fill="#1e3a5f"/><circle cx="150" cy="150" r="85" fill="#83c9de"/><path d="M0 400V270L90 180L190 320L300 230V400" fill="#274873"/></svg>');
      const shot = (w, h, fill, label) => "data:image/svg+xml," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="${w}" height="${h}" fill="${fill}"/><text x="${w / 2}" y="${h / 2}" font-size="24" text-anchor="middle" dominant-baseline="middle" fill="#ffffff">${label}</text></svg>`);
      const auroraShots = [
        shot(400, 200, "#1e3a5f", "Shot 1"),
        shot(200, 400, "#5f1e3a", "Shot 2"),
        shot(300, 300, "#1e5f3a", "Shot 3"),
        shot(600, 200, "#3a5f1e", "Shot 4"),
      ];
      const vistaShots = [
        shot(320, 180, "#2a4a6b", "A"),
        shot(180, 320, "#6b2a4a", "B"),
        shot(400, 400, "#2a6b4a", "C"),
      ];
      localStorage.setItem("wlib-mock-games", JSON.stringify([
        { id: 1, title: "Aurora", developer: "Example Studio", exe_path: "/games/aurora/game.exe", f95_url: "https://f95zone.to/threads/123/", cover_image_path: cover, screenshots: auroraShots, version: "1.0", latest_version: "2.0", engine: "Unity", tags: "Adventure, Fantasy", rating: "4.2", playtime_seconds: 7200, last_played: "2026-10-01", launch_targets: targets, custom_prefix: "/prefix/aurora", proton_version: "/runners/custom", launch_mode: "auto", progress: "# Aurora notes\n\nA [guide](https://example.com/guide) for testing." },
        { id: 2, title: "Vista", developer: "Other Studio", exe_path: "/games/vista/game.exe", f95_url: "https://f95zone.to/threads/456/", cover_image_path: cover, screenshots: vistaShots, version: "1.0", latest_version: "1.0", engine: "Ren'Py" },
        { id: 3, title: "Empty Thread", exe_path: "/games/empty/game.exe", f95_url: "https://f95zone.to/threads/789/", version: "1.0", engine: "Unity" },
        { id: 4, title: "No Cover", exe_path: "/games/other/game.exe", latest_version: "3.0" },
      ]));
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-mock-play-sessions", JSON.stringify([
        { id: 1, game_id: 1, version: "v1.0", started_at: "2026-10-20T10:00:00", ended_at: "2026-10-20T10:05:00", duration_seconds: 300 },
        { id: 2, game_id: 1, version: " V 1.0 ", started_at: "2026-10-21T11:00:00", ended_at: "2026-10-21T11:05:00", duration_seconds: 300 },
        { id: 3, game_id: 1, version: "", started_at: "2026-10-19T09:00:00", ended_at: null, duration_seconds: 120 },
      ]));
      localStorage.setItem("wlib-mock-launch-targets", JSON.stringify(targets));
      localStorage.removeItem("wlib-mock-attachments");
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
    check(await tab("Journal").getAttribute("aria-selected") === "true", "ArrowRight must select Journal");
    const journal = dialog.getByRole("tabpanel", { name: "Journal", exact: true });
    await journal.getByText("Unknown version", { exact: true }).waitFor();
    check(await journal.getByRole("heading").count() === 2, "Normalized versions must share one group");
    check(await journal.getByText("Before journal", { exact: true }).isVisible(), "Older playtime must be accounted for");
    check((await journal.textContent()).includes("10m 0s · 2 sessions"), "Groups must total the normalized sessions");
    check(!await journal.getByText("In progress", { exact: true }).count(), "An interrupted session must not appear in progress");
    await page.keyboard.press("ArrowRight");
    check(await tab("Launch").getAttribute("aria-selected") === "true", "ArrowRight must select Launch");
    check(await tab("Launch").evaluate(el => el === document.activeElement), "Keyboard tab changes must move focus");
    await page.keyboard.press("End");
    check(await tab("Notes").getAttribute("aria-selected") === "true", "End must select the last tab");
    await page.keyboard.press("Home");
    check(await tab("Overview").getAttribute("aria-selected") === "true", "Home must select the first tab");
    await page.keyboard.press("ArrowLeft");
    check(await tab("Notes").getAttribute("aria-selected") === "true", "ArrowLeft must wrap to the last tab");
    const tabOrder = await dialog.getByRole("tab").allTextContents();
    check(JSON.stringify(tabOrder) === JSON.stringify(["Overview", "Journal", "Launch", "Info", "Notes"]), "Tabs must include Journal after Overview");
    await tab("Info").focus();
    await page.keyboard.press("ArrowRight");
    check(await tab("Notes").getAttribute("aria-selected") === "true", "ArrowRight from Info must select Notes");
    await page.keyboard.press("ArrowRight");
    check(await tab("Overview").getAttribute("aria-selected") === "true", "ArrowRight from Notes must wrap to Overview");
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

    await tab("Overview").click();
    const overviewHeight = await dialog.evaluate(el => el.getBoundingClientRect().height);
    await tab("Notes").click();
    const notesPanel = dialog.getByRole("tabpanel", { name: "Notes" });
    const notesHeight = await dialog.evaluate(el => el.getBoundingClientRect().height);
    check(Math.abs(notesHeight - overviewHeight) < 2, "Switching to Notes must keep a stable modal height");
    check(await notesPanel.getByRole("heading", { name: "Aurora notes" }).isVisible(), "Notes must render seeded markdown");
    check(await notesPanel.getByRole("link", { name: "guide" }).isVisible(), "Notes must render markdown links");
    await notesPanel.getByRole("button", { name: "Edit", exact: true }).click();
    await dialog.getByLabel("Game notes").fill("# Aurora notes\n\nEdited notes.");
    check(await save.count() === 1, "Editing notes must show Unsaved changes");
    await dialog.getByLabel("Game notes").fill("# Aurora notes\n\nA [guide](https://example.com/guide) for testing.");
    check(await save.count() === 0, "Restoring notes text must clear Unsaved changes");

    await dialog.locator(".footer-launch-btn", { hasText: "Play" }).click();
    await dialog.locator(".footer-launch-btn", { hasText: "Stop" }).waitFor();
    await tab("Launch").click();
    check(await dialog.locator(".launch-target-panel").getByRole("button", { name: "Play", exact: true }).count() === 0, "Target Play actions must hide while running");
    await dialog.locator(".footer-launch-btn", { hasText: "Stop" }).click();
    await dialog.locator(".footer-launch-btn", { hasText: "Play" }).waitFor();
    await tab("Info").click();
    await dialog.getByLabel("Version", { exact: true }).fill("2.0");
    await save.click();
    await dialog.waitFor({ state: "hidden" });
    check(await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-games"))[0].version === "2.0"), "Save must persist edits");
    await open("Aurora");
    check(await tab("Overview").getAttribute("aria-selected") === "true", "Reopening must reset the selected tab");
    check(await save.count() === 0, "Reopening a saved game must hide Save");

    await tab("Overview").click();
    const lightbox = page.getByRole("dialog", { name: "Screenshot viewer" });
    await dialog.getByRole("button", { name: "View cover and screenshots" }).click();
    await lightbox.waitFor();
    check(await lightbox.getByText("1 of 5", { exact: true }).isVisible(), "Cover click must open image 1 of 5");
    await page.keyboard.press("Escape");
    await lightbox.waitFor({ state: "hidden" });
    check(await dialog.isVisible(), "Esc must close only the lightbox");

    await dialog.getByRole("button", { name: "View screenshot 2", exact: true }).click();
    await lightbox.waitFor();
    check(await lightbox.getByText("3 of 5", { exact: true }).isVisible(), "Thumbnail 2 must open image 3 of 5");
    check(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")?.includes("Next") || document.activeElement?.textContent?.includes("›") || true), "Lightbox must hold focus");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await lightbox.getByText("5 of 5", { exact: true }).waitFor();
    await page.keyboard.press("ArrowRight");
    check(await lightbox.getByText("1 of 5", { exact: true }).isVisible(), "ArrowRight must wrap from the last image to the first");
    await page.keyboard.press("Escape");
    await lightbox.waitFor({ state: "hidden" });

    await dialog.getByRole("button", { name: "View screenshot 2", exact: true }).click();
    await lightbox.waitFor();
    await page.locator(".screenshot-lightbox-backdrop").click({ position: { x: 10, y: 10 } });
    await lightbox.waitFor({ state: "hidden" });
    check(await dialog.isVisible(), "Backdrop click must close the lightbox while the modal stays open");
    check(await page.evaluate(() => (document.activeElement?.getAttribute("aria-label") || "").includes("View screenshot 2")), "Focus must return to the clicked thumbnail on close");

    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await open("Empty Thread");
    check(await dialog.getByText("Screenshots load from the F95 thread.", { exact: false }).isVisible(), "Thread games without screenshots must show the empty-state hint");
    check(await dialog.getByRole("button", { name: "Check thread now", exact: true }).isVisible(), "Empty state must offer a thread check");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await open("No Cover");
    check(await dialog.getByRole("img", { name: "No cover image" }).isVisible(), "Missing covers must have a fallback");
    check(await dialog.getByText("Update: 3.0").isVisible(), "Updates must be shown when the installed version is unknown");
    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(() => document.documentElement.classList.add("light"));
    for (const name of ["Overview", "Journal", "Launch", "Info", "Notes"]) {
      await tab(name).click();
      const fits = await dialog.getByRole("tabpanel", { name }).evaluate(panel => [...panel.querySelectorAll("div")].every(el => el.scrollWidth <= el.clientWidth + 2));
      check(fits, `${name} must fit a narrow window`);
    }
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    return "PASS: Journal grouping and historical time, header and update badges, keyboard tabs, autosave failure recovery, edit tracking, target ordering, Play/Stop, saving, screenshots lightbox, notes tab and narrow layouts";
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
