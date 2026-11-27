// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/onboarding_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const previousViewport = page.viewportSize();
  const keys = [
    "wlib-mock-onboarding",
    "wlib-mock-settings",
    "wlib-mock-games",
    "wlib-mock-catalog-installed",
    "wlib-ui-motion",
    "wlib-theme",
    "wlib-tv-mode",
  ];
  const previous = await page.evaluate(
    (keys) => keys.map((key) => [key, localStorage.getItem(key)]),
    keys,
  );
  const errors = [];
  const onPageError = (err) => errors.push(String(err));
  page.on("pageerror", onPageError);

  const overlay = () => page.getByTestId("onboarding-overlay");
  const dialog = () => overlay().getByRole("dialog");
  const railState = (id) => dialog().locator(`[data-rail-step="${id}"]`).getAttribute("data-rail-state");
  const railName = (id) => dialog().locator(`[data-rail-step="${id}"] .sr-only`).textContent();
  const railHasCheck = (id) => dialog().locator(`[data-rail-step="${id}"] .tabler-icon-check`).count();

  const startFresh = async (extra = {}) => {
    await page.evaluate((opts) => {
      delete window.pywebview;
      localStorage.setItem("wlib-theme", opts.theme || "dark");
      localStorage.setItem("wlib-ui-motion", opts.motion ?? "off");
      localStorage.setItem("wlib-mock-games", JSON.stringify(opts.games ?? []));
      localStorage.setItem("wlib-mock-settings", JSON.stringify(opts.settings ?? { catalog_enabled: true, lc_enabled: false }));
      if (opts.catalogInstalled === false) localStorage.setItem("wlib-mock-catalog-installed", "0");
      else localStorage.removeItem("wlib-mock-catalog-installed");
      localStorage.setItem("wlib-mock-onboarding", JSON.stringify(opts.onboarding ?? {}));
    }, extra);
    const url = new URL(previousUrl);
    if (extra.search !== undefined) url.search = extra.search;
    url.hash = extra.hash || "/";
    await page.goto(url.toString());
    await page.reload();
  };

  let phase = "init";
  try {
    await page.setViewportSize({ width: 1280, height: 820 });

    // 1. Shows on empty mock state, Tab stays inside, Escape on Welcome keeps it open, motion off disables transition.
    phase = "welcome-and-focus";
    await startFresh({ motion: "off" });
    await dialog().waitFor();
    await page.getByRole("heading", { name: "Welcome to wLib", exact: true }).waitFor();

    // Motion off: transition-duration is 0s.
    const motionOff = await page.evaluate(() => document.documentElement.classList.contains("motion-off"));
    check(motionOff, "motion-off class not active");

    // Escape on Welcome does NOT close onboarding.
    await page.keyboard.press("Escape");
    await page.waitForTimeout(100);
    check(await overlay().isVisible(), "Escape on Welcome closed onboarding");

    // Repeated Tab keeps focus strictly inside the onboarding dialog.
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(() => {
        const dlg = document.querySelector('[data-testid="onboarding-overlay"] [role="dialog"]');
        return !!dlg && dlg.contains(document.activeElement);
      });
      check(inside, `Tab #${i + 1} escaped onboarding dialog`);
    }

    // 2. Skip on Welcome closes overlay and saves "skipped" in mock; reload shows no overlay.
    phase = "welcome-skip";
    await dialog().getByRole("button", { name: "Skip setup", exact: true }).click();
    await overlay().waitFor({ state: "hidden" });
    const afterSkip = await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}"));
    check(afterSkip.status === "skipped", `Expected skipped, got ${JSON.stringify(afterSkip)}`);
    await page.reload();
    await page.waitForTimeout(300);
    check(!(await overlay().count()), "Overlay reopened after reload following Skip");

    // 3. Extension add queue + pairing dialog layering above overlay.
    phase = "extension-queue-and-pairing";
    await startFresh({ motion: "off" });
    await dialog().waitFor();

    // Dispatch extension add while onboarding is open -> toast appears, Add Game modal does not open yet.
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("wlib-extension-add", {
        detail: { title: "Queued Game", url: "https://f95zone.to/threads/queued-game.9999/" },
      }));
    });
    await page.getByText("Queued Game from the browser extension opens when setup ends.", { exact: false }).waitFor();
    check(!(await page.getByRole("heading", { name: "Add New Game", exact: true }).count()), "Add Game opened while onboarding was up");

    // Trigger a pairing confirmation dialog above the overlay -> ConfirmDialog appears on top, focus returns after answer.
    await page.evaluate(async () => {
      const mod = await import("/src/utils/confirm.ts");
      window.__pairAnswer = mod.confirmAction({
        title: "Allow browser extension",
        message: "Allow Firefox extension ABCD-12 to connect to wLib?",
        confirmLabel: "Allow",
      });
    });
    const confirmHeading = page.getByRole("heading", { name: "Allow browser extension", exact: true });
    await confirmHeading.waitFor();
    const layers = await page.evaluate(() => {
      const ov = document.querySelector('[data-testid="onboarding-overlay"]');
      const cf = document.querySelector('[role="alertdialog"], [data-testid="confirm-dialog"]') ||
        [...document.querySelectorAll('[aria-modal="true"]')].find((el) => !el.closest('[data-testid="onboarding-overlay"]'));
      return {
        ovZ: ov ? Number(getComputedStyle(ov).zIndex) : 0,
        cfZ: cf ? Number(getComputedStyle(cf.closest('.fixed') || cf).zIndex) : 0,
      };
    });
    check(layers.cfZ > layers.ovZ, `ConfirmDialog z-index (${layers.cfZ}) must be above onboarding (${layers.ovZ})`);
    await page.getByRole("button", { name: "Allow", exact: true }).click();
    await confirmHeading.waitFor({ state: "hidden" });
    const returnedFocus = await page.evaluate(() => {
      const dlg = document.querySelector('[data-testid="onboarding-overlay"] [role="dialog"]');
      return !!dlg && dlg.contains(document.activeElement);
    });
    check(returnedFocus, "Focus did not return to onboarding after closing ConfirmDialog");

    // Skip now -> queued Add Game dialog opens!
    await dialog().getByRole("button", { name: "Skip setup", exact: true }).click();
    await overlay().waitFor({ state: "hidden" });
    await page.getByRole("heading", { name: /Add/i }).first().waitFor({ timeout: 5000 });

    // 4. Full 6-step flow: Welcome -> Catalog -> LewdCorner -> Import -> Extension -> Done, plus resume check.
    phase = "catalog-and-lc-steps";
    await startFresh({
      motion: "off",
      catalogInstalled: true,
      games: [{ id: 1, title: "Existing Game", exe_path: "/games/existing.exe", version: "1.0" }],
    });
    await dialog().waitFor();
    await dialog().getByRole("button", { name: "Get started", exact: true }).click();
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();

    // Live region announced Step 2 of 6, Game catalog.
    const ann2 = await page.getByTestId("onboarding-announcer").textContent();
    check(ann2?.includes("Step 2 of 6, Game catalog"), `Unexpected announcement: ${ann2}`);

    // Before the switch is used, the help says what On and Off do. A local copy exists, so Off names the deletion.
    const catalogHelp = () => dialog().locator("#onboarding-catalog-help").textContent();
    const benefits = () => page.getByTestId("catalog-benefits");
    const helpBefore = await catalogHelp();
    check(helpBefore?.includes("When on, wLib checks for a new catalog at most twice a day."), `Catalog help must say what On does: ${helpBefore}`);
    check(helpBefore?.includes("When off, wLib deletes its local copy."), `Catalog help must name the deletion when a copy exists: ${helpBefore}`);
    check((await benefits().getAttribute("data-catalog-tone")) === "brand", "Catalog card must start as What you get");
    check(await benefits().getByRole("heading", { name: "What you get", exact: true }).count() === 1, "What you get heading missing");

    // Turn catalog off -> saves immediately (no ConfirmDialog); the card lists what is missing; no past-tense note.
    const catalogSwitch = dialog().getByRole("switch", { name: "Download the game catalog" });
    check(await catalogSwitch.isChecked(), "Catalog switch should start checked");
    await dialog().locator("label:has(#onboarding-catalog-enabled)").click();
    await dialog().getByText("Saved", { exact: true }).waitFor();
    await benefits().getByRole("heading", { name: "Without the catalog", exact: true }).waitFor();
    check((await benefits().getAttribute("data-catalog-tone")) === "muted", "Catalog card must be muted when off");
    const offItems = await benefits().locator("li").allTextContents();
    check(offItems.length === 4 && offItems.every((item) => item.trim().startsWith("No ")), `Off list must name 4 missing items: ${offItems}`);
    check(offItems[0]?.includes("Discover"), `Off list must start with Discover: ${offItems}`);
    check(!(await page.getByTestId("catalog-deleted-note").count()), "Past-tense deletion note must be gone");
    check(!(await dialog().getByText(/deleted/i).count()), "Catalog step must not use past tense 'deleted'");
    check(await dialog().getByText("What wLib sends", { exact: true }).count() === 1, "Privacy card must stay");
    check(
      await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-settings") || "{}").catalog_enabled === false),
      "catalog_enabled was not saved as false",
    );

    // Next releases catalog hold once and opens LewdCorner step.
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "LewdCorner", exact: true }).waitFor();
    const afterCatalogNext = await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}"));
    check(afterCatalogNext.release_calls === 1, `Expected release_calls=1, got ${afterCatalogNext.release_calls}`);

    // Because catalog is off, the catalog-off note is visible on LewdCorner step.
    await page.getByTestId("lc-catalog-off-note").waitFor();
    // Toggle Use LewdCorner on -> saves immediately.
    check(!(await dialog().getByRole("switch", { name: "Use LewdCorner" }).isChecked()), "LC switch should start unchecked");
    await dialog().locator("label:has(#onboarding-lc-enabled)").click();
    await dialog().getByText("Saved", { exact: true }).waitFor();
    check(
      await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-settings") || "{}").lc_enabled === true),
      "lc_enabled was not saved as true",
    );

    // Go back to Catalog, turn catalog ON, go to LewdCorner -> note disappears!
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    await dialog().locator("label:has(#onboarding-catalog-enabled)").click();
    await dialog().getByText("Saved", { exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "LewdCorner", exact: true }).waitFor();
    check(!(await page.getByTestId("lc-catalog-off-note").count()), "Catalog-off note should hide when catalog is on");
    // Turn catalog back off so Done summary verifies Catalog=Off, LC=On later? Let's keep Catalog=On, LC=On!

    // 5. Import step: shows existing games note, busy disables nav buttons during import, Next without import keeps library unchanged.
    phase = "import-step";
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    const existingNote = await page.getByTestId("import-existing-games-note").textContent();
    check(existingNote?.includes("Your library already has 1 game"), `Missing existing games note: ${existingNote}`);

    // First verify Next without import leaves mock library unchanged.
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    check(
      await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-games") || "[]").length === 1),
      "Next without import modified mock library",
    );

    // Go back to Import step and run a slow mock import to verify busy disabling + result.
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    await page.evaluate(() => {
      window.pywebview = {
        api: {
          check_path: async () => ({ ok: true, reason: "" }),
          inspect_external_library: async () => ({
            success: true,
            available_sections: ["user_state"],
            counts: { total_games: 3, matched_games: 0, new_games: 3, ambiguous_games: 0, skipped_no_identity: 0, dropped_paths: 0, covers_to_copy: 0 },
            warnings: [],
          }),
          import_external_library: () => new Promise((resolve) => {
            window.__finishImport = () => resolve({ success: true, created: 3, updated: 0, skipped: 0, metadata_check: "none" });
          }),
          set_onboarding_step: async (step) => {
            const cur = JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}");
            localStorage.setItem("wlib-mock-onboarding", JSON.stringify({ ...cur, step }));
            return { success: true, step };
          },
          get_extension_pairing: async () => ({
            success: true,
            paired: window.__mockPaired || [],
          }),
          get_platform_capabilities: async () => ({
            platform: "linux",
            extension_dir: "/home/player/.local/share/wLib/extension",
          }),
          finish_onboarding: async (status) => {
            const cur = JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}");
            localStorage.setItem("wlib-mock-onboarding", JSON.stringify({ ...cur, status, step: "", catalog_held: false }));
            return { success: true, status, catalog_started: false };
          },
          get_games: async () => JSON.parse(localStorage.getItem("wlib-mock-games") || "[]"),
        },
      };
      window.__mockPaired = [];
    });
    const pathInput = dialog().getByPlaceholder("Path to the source library file", { exact: true });
    const inspectBtn = dialog().getByRole("button", { name: "Inspect Library", exact: true });
    const isPrimary = (locator) => locator.evaluate((el) => el.hasAttribute("data-controller-primary"));
    await pathInput.fill("/mock/f95checker.db");
    check(await isPrimary(inspectBtn), "Inspect Library must be the controller primary before inspect");
    await inspectBtn.click();
    await dialog().getByText("New: 3", { exact: true }).waitFor();

    // One primary: the footer runs the import; the panel's own button is hidden.
    const importPrimary = dialog().getByRole("button", { name: "Import 3 games", exact: true });
    await importPrimary.waitFor();
    check(!(await dialog().getByRole("button", { name: "Import Selected" }).count()), "Onboarding must hide the panel's Import Selected button");
    check(!(await dialog().getByRole("button", { name: "Next", exact: true }).count()), "Next must not show while a preview waits");
    check(!(await isPrimary(inspectBtn)), "Inspect Library must not stay the controller primary after inspect");
    check(await isPrimary(importPrimary), "Footer import must be the controller primary");
    await dialog().getByText("wLib makes no backup first. You cannot undo this.", { exact: true }).waitFor();
    // Skip setup stays while a preview waits; Skip import sits next to the primary.
    check(await dialog().getByRole("button", { name: "Skip setup", exact: true }).isVisible(), "Skip setup must stay while a preview waits");
    const footerOrder = await dialog().locator("footer button").evaluateAll((els) => els.map((el) => el.textContent.trim()));
    check(
      JSON.stringify(footerOrder) === JSON.stringify(["Back", "Skip import", "Import 3 games", "Skip setup"]),
      `Footer Tab order must be Back, Skip import, primary, Skip setup: ${footerOrder}`,
    );
    const skipLeft = await dialog().getByRole("button", { name: "Skip setup", exact: true }).boundingBox();
    const backLeft = await dialog().getByRole("button", { name: "Back", exact: true }).boundingBox();
    check(skipLeft && backLeft && skipLeft.x < backLeft.x, "Skip setup must stay at the left of the footer");

    // Skip import moves on without importing and drops the preview.
    await dialog().getByRole("button", { name: "Skip import", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    check(!(await page.evaluate(() => typeof window.__finishImport === "function")), "Skip import must not run the import");
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    check(await dialog().getByRole("button", { name: "Next", exact: true }).isVisible(), "Next must return after Skip import");
    check(!(await dialog().getByRole("button", { name: "Skip import", exact: true }).count()), "Skip import must go after Skip import");

    await dialog().getByPlaceholder("Path to the source library file", { exact: true }).fill("/mock/f95checker.db");
    await dialog().getByRole("button", { name: "Inspect Library", exact: true }).click();
    await importPrimary.click();
    await page.waitForFunction(() => typeof window.__finishImport === "function");

    check(await dialog().getByRole("button", { name: "Back", exact: true }).isDisabled(), "Back must be disabled while import runs");
    check(await dialog().getByRole("button", { name: "Importing...", exact: true }).isDisabled(), "Footer primary must be disabled while import runs");
    check(await dialog().getByRole("button", { name: "Skip import", exact: true }).isDisabled(), "Skip import must be disabled while import runs");

    await page.evaluate(() => window.__finishImport());
    await dialog().getByText("Imported library. Created 3", { exact: false }).waitFor();
    // The step stays open with the result; the primary is Next again.
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    check(await dialog().getByRole("button", { name: "Next", exact: true }).isEnabled(), "Next should re-enable after import");
    check(!(await dialog().getByRole("button", { name: "Skip import", exact: true }).count()), "Skip import must go after the import");

    // 6. Extension step: live pairing status updates from Waiting to Paired with Firefox.
    phase = "extension-step";
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    const pill = page.getByTestId("extension-pairing-pill");
    check((await pill.textContent())?.includes("Waiting for the extension"), `Expected waiting pill, got ${await pill.textContent()}`);

    await page.evaluate(() => {
      window.__mockPaired = [{ id: "ABCD-12", browser: "Firefox", origin: "moz-extension://x" }];
      window.dispatchEvent(new Event("wlib-extension-pairing"));
    });
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="extension-pairing-pill"]')?.textContent?.includes("Paired with Firefox"),
    );

    // Done counts the import from step 5.
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "You're all set", exact: true }).waitFor();
    const importRow = await dialog().locator('[data-summary-row="import"]').textContent();
    check(importRow?.includes("3 games imported"), `Done summary import mismatch: ${importRow}`);
    // Everything set: the confident ending, every passed step has a check, no row actions.
    check(!(await page.getByTestId("done-left-text").count()), "All set must not say things are left for later");
    check(!(await dialog().locator(".done-badge--calm").count()), "All set must keep the brand badge");
    check(!(await dialog().locator("[data-summary-action]").count()), "All set must show no row actions");
    for (const id of ["welcome", "catalog", "lc", "import", "extension"]) {
      check((await railState(id)) === "done", `Rail ${id} must be done when all is set, got ${await railState(id)}`);
    }
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();

    // 7. Resume check: reloading on Extension step reopens on Extension step!
    phase = "resume-step";
    await page.evaluate(() => { delete window.pywebview; });
    await page.reload();
    await dialog().waitFor();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    // The reload cleared this run's import count, but the library still has a
    // game, so import stays set after a restart.
    await page.waitForFunction(() => document.querySelector('[data-rail-step="import"]')?.getAttribute("data-rail-state") === "done");
    check((await railName("import"))?.trim() === "Import from other apps, done", `Rail import name after restart: ${await railName("import")}`);
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    const restartRow = await dialog().locator('[data-summary-row="import"]').textContent();
    check(restartRow?.includes("1 game in your library"), `Done import row after restart: ${restartRow}`);
    check((await page.getByTestId("done-left-text").textContent())?.trim() === "1 thing is left for later.", "Done after restart must count 1 thing left");
    // An empty library: import is not set again.
    await page.evaluate(() => localStorage.setItem("wlib-mock-games", "[]"));
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await page.reload();
    await dialog().waitFor();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();

    // 8. Done step: summary matches choices and Go to library saves done and opens Library.
    phase = "done-step";
    // An empty library, no import in this run, and no paired browser.
    check((await railState("import")) === "not-set", `Rail import must be not set on Extension, got ${await railState("import")}`);
    check((await railName("import"))?.trim() === "Import from other apps, not set", `Rail import name: ${await railName("import")}`);
    check(!(await railHasCheck("import")), "A not-set rail step must not show a check");
    check((await railState("lc")) === "done", "LewdCorner is a choice and must show done");
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    check((await page.getByTestId("done-left-text").textContent())?.trim() === "2 things are left for later.", "Done must count 2 things left");
    check(await dialog().locator(".done-badge--calm").count() === 1, "Done with things left must use the calm badge");
    check((await railState("extension")) === "not-set", "Rail extension must be not set on Done when unpaired");
    check((await railName("extension"))?.trim() === "Browser extension, not set", `Rail extension name: ${await railName("extension")}`);
    check((await dialog().locator('[data-summary-row="import"]').textContent())?.includes("None imported"), "Done import row must say None imported");
    check(
      await dialog().getByRole("button", { name: "Go to library", exact: true }).evaluate((el) => el.hasAttribute("data-controller-primary")),
      "Go to library must be the controller primary on Done",
    );
    // Row actions are real buttons in Tab order: the first Tab from the heading reaches Import games.
    await page.locator("#onboarding-step-title").focus();
    await page.keyboard.press("Tab");
    check(
      (await page.evaluate(() => document.activeElement?.textContent?.trim())) === "Import games",
      "Tab from the Done heading must reach Import games",
    );
    // Pair now goes back to the extension step; Next comes back to Done.
    await dialog().getByRole("button", { name: "Pair now", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    check(
      (await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}").step)) === "extension",
      "Pair now must save the extension step",
    );
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    // Import games goes back to the import step; Next walks forward again.
    await dialog().getByRole("button", { name: "Import games", exact: true }).click();
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    check((await dialog().locator('[data-summary-row="catalog"]').textContent())?.includes("On"), "Done summary catalog mismatch");
    check((await dialog().locator('[data-summary-row="lc"]').textContent())?.includes("On"), "Done summary LC mismatch");
    check(!(await dialog().getByRole("button", { name: "Skip setup", exact: true }).count()), "Done step must not offer Skip setup");

    await dialog().getByRole("button", { name: "Go to library", exact: true }).click();
    await overlay().waitFor({ state: "hidden" });
    const afterDone = await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}"));
    check(afterDone.status === "done", `Expected done, got ${JSON.stringify(afterDone)}`);
    await page.reload();
    await page.waitForTimeout(300);
    check(!(await overlay().count()), "Overlay reopened after reload following Done");

    // 9. Settings replay row + search for "onboarding" + replay leaves saved state unchanged.
    phase = "settings-replay";
    const settingsUrl = new URL(previousUrl);
    settingsUrl.hash = "/settings";
    await page.goto(settingsUrl.toString());
    await page.reload();
    await page.locator('#settings-search-desktop').waitFor();
    await page.locator('#settings-search-desktop').fill("onboarding");
    const replayRow = page.locator('[data-settings-row="onboarding-replay"]');
    await replayRow.waitFor({ state: "visible" });
    await replayRow.getByRole("button", { name: "Show setup again", exact: true }).click();
    await dialog().waitFor();
    await page.getByRole("heading", { name: "Welcome to wLib", exact: true }).waitFor();

    // Move to Catalog step during replay, verify current setting values, then Skip -> state stays "done" (not "skipped")!
    await dialog().getByRole("button", { name: "Get started", exact: true }).click();
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Skip setup", exact: true }).click();
    await overlay().waitFor({ state: "hidden" });
    const afterReplaySkip = await page.evaluate(() => JSON.parse(localStorage.getItem("wlib-mock-onboarding") || "{}"));
    check(afterReplaySkip.status === "done", `Replay skip must keep saved status 'done', got ${JSON.stringify(afterReplaySkip)}`);

    // Start replay again and reload mid-replay -> reload must show NO overlay!
    await replayRow.getByRole("button", { name: "Show setup again", exact: true }).click();
    await dialog().waitFor();
    await dialog().getByRole("button", { name: "Get started", exact: true }).click();
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    await page.reload();
    await page.waitForTimeout(300);
    check(!(await overlay().count()), "Reload during replay must not reopen onboarding");

    // 10. Extension page still renders Chrome and Firefox steps unchanged.
    phase = "extension-page";
    const extUrl = new URL(previousUrl);
    extUrl.hash = "/extension";
    await page.goto(extUrl.toString());
    await page.reload();
    await page.getByRole("heading", { name: "Browser Extension", exact: true }).waitFor();
    await page.getByText("Chrome / Chromium / Edge", { exact: true }).waitFor();
    await page.getByText("Firefox", { exact: true }).waitFor();

    // 11. Controller flow: complete all 6 steps with directional moves, A, B, and LB/RB only, including an import.
    phase = "controller-flow";
    await startFresh({ motion: "off" });
    await dialog().waitFor();
    const dispatch = async (action) => page.evaluate(async (act) => {
      const modUrl = performance.getEntriesByType("resource").map((x) => x.name).find((x) => /\/src\/utils\/controllerActions\.ts/.test(x));
      (await import(modUrl)).dispatchController(act, () => {});
    }, action);

    // Welcome -> RB moves to Catalog
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    // B goes back to Welcome
    await dispatch({ type: "back" });
    await page.getByRole("heading", { name: "Welcome to wLib", exact: true }).waitFor();
    // B on Welcome stays on Welcome
    await dispatch({ type: "back" });
    await page.getByRole("heading", { name: "Welcome to wLib", exact: true }).waitFor();
    // Move to Get started + A
    await dialog().getByRole("button", { name: "Get started", exact: true }).focus();
    await dispatch({ type: "activate" });
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    // Toggle catalog switch with directional focus + A
    await dialog().getByRole("switch", { name: "Download the game catalog" }).focus();
    await dispatch({ type: "activate" });
    await dialog().getByText("Saved", { exact: true }).waitFor();
    // RB -> LewdCorner
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "LewdCorner", exact: true }).waitFor();
    // LB -> Catalog, RB -> LewdCorner
    await dispatch({ type: "prevTab" });
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "LewdCorner", exact: true }).waitFor();
    // RB -> Import
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    // Run a controller import: pick Playnite with A, fill path, focus Inspect Library -> A, then RB imports.
    const hintLabels = async () => page.evaluate(async () => {
      const modUrl = performance.getEntriesByType("resource").map((x) => x.name).find((x) => /\/src\/utils\/controllerActions\.ts/.test(x));
      return (await import(modUrl)).currentHints().map((h) => `${h.button}:${h.label}`);
    });
    await dialog().getByRole("button", { name: "Playnite", exact: true }).focus();
    await dispatch({ type: "activate" });
    const ctrlPath = dialog().getByPlaceholder("Path to the source library file", { exact: true });
    await ctrlPath.fill("/mock/ctrl-playnite.db");
    await dialog().getByRole("button", { name: "Inspect Library", exact: true }).focus();
    await dispatch({ type: "activate" });
    await dialog().getByRole("button", { name: "Import 146 games", exact: true }).waitFor();
    const waitingHints = await hintLabels();
    check(waitingHints.includes("RB:Import"), `RB hint must say Import while a preview waits: ${waitingHints}`);
    // RB matches the footer primary: it imports and stays on the step.
    await dispatch({ type: "nextTab" });
    await dialog().getByText("Imported library.", { exact: false }).waitFor();
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Next", exact: true }).waitFor();
    check((await hintLabels()).includes("LB / RB:Step"), "RB hint must return to Step after the import");
    // RB -> Extension -> RB -> Done
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    // The browser mock import adds no games, so both import and extension are left.
    check((await page.getByTestId("done-left-text").textContent())?.trim() === "2 things are left for later.", "Controller Done must count 2 things left");
    check((await railState("import")) === "not-set", "Rail import must be not set after a mock import of 0 games");
    // The first D-pad move lands on the primary, Go to library. Up reaches Pair now;
    // A opens the extension step; RB returns to Done.
    await page.locator("#onboarding-step-title").focus();
    await dispatch({ type: "move", direction: "down" });
    check(await page.evaluate(() => document.activeElement?.textContent?.trim() === "Go to library"), "First D-pad move on Done must land on Go to library");
    for (let i = 0; i < 4; i++) {
      if (await page.evaluate(() => document.activeElement?.textContent?.trim() === "Pair now")) break;
      await dispatch({ type: "move", direction: "up" });
    }
    check(await page.evaluate(() => document.activeElement?.textContent?.trim() === "Pair now"), "D-pad must reach Pair now");
    await dispatch({ type: "activate" });
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    // Focus Go to library via directional move / focus + A
    await dispatch({ type: "move", direction: "down" });
    await dialog().getByRole("button", { name: "Go to library", exact: true }).focus();
    await dispatch({ type: "activate" });
    await overlay().waitFor({ state: "hidden" });

    // 12. Skip import path: inspect, Skip import, Done says None imported. Also at 800x600.
    phase = "skip-import";
    await page.setViewportSize({ width: 800, height: 600 });
    await startFresh({ motion: "off", onboarding: { status: "", step: "import" } });
    await page.getByRole("heading", { name: "Import from other apps", exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Playnite", exact: true }).click();
    await dialog().getByPlaceholder("Path to the source library file", { exact: true }).fill("/mock/skip-playnite.db");
    await dialog().getByRole("button", { name: "Inspect Library", exact: true }).click();
    const skipPrimary = dialog().getByRole("button", { name: "Import 146 games", exact: true });
    await skipPrimary.waitFor();
    check(await skipPrimary.isVisible(), "Footer import must be visible at 800x600");
    await dialog().getByRole("button", { name: "Skip import", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    // Compact rail: a skipped import is a hollow "not set" segment with its own name.
    check(await dialog().locator('.rail-compact[role="group"] ol.rail-segments').count() === 1, "Compact rail must be a named group with a list");
    check((await railState("import")) === "not-set", `Compact rail import must be not set, got ${await railState("import")}`);
    check((await railName("import"))?.trim() === "Import from other apps, not set", `Compact rail import name: ${await railName("import")}`);
    check((await railState("catalog")) === "done", "Compact rail catalog must be done");
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    const skippedRow = await dialog().locator('[data-summary-row="import"]').textContent();
    check(skippedRow?.includes("None imported"), `Skip import must leave None imported, got ${skippedRow}`);

    // 13. Scroll cue at 800x600: the bottom fade shows while more is below and goes at the end.
    phase = "scroll-fade";
    const fade = (edge) => page.getByTestId(`onboarding-scroll-fade-${edge}`);
    const fadeOn = async (edge) => (await fade(edge).evaluate((el) => el.classList.contains("is-visible")));
    const body = page.getByTestId("onboarding-body");
    const waitFade = async (edge, on, message) => {
      for (let i = 0; i < 20 && (await fadeOn(edge)) !== on; i++) await page.waitForTimeout(50);
      check((await fadeOn(edge)) === on, message);
    };
    await startFresh({ motion: "off", onboarding: { status: "", step: "done" } });
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    check(await fade("bottom").evaluate((el) => getComputedStyle(el).pointerEvents === "none"), "Scroll fade must not take pointer events");
    await waitFade("bottom", true, "Bottom fade must show on Done at 800x600 while more is below");
    check(!(await fadeOn("top")), "Top fade must stay hidden at the top");
    await body.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    await waitFade("bottom", false, "Bottom fade must hide at the end of the body");
    await waitFade("top", true, "Top fade must show after scrolling down");
    // Step change resets the cue for the new step.
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await waitFade("top", false, "Top fade must hide after a step change");
    // Only one shared Open extension folder button outside Gaming Mode.
    check((await dialog().getByRole("button", { name: "Open extension folder", exact: true }).count()) === 1, "Extension step must show one Open extension folder button");
    // A roomy window has no fade.
    await page.setViewportSize({ width: 1280, height: 820 });
    await waitFade("bottom", false, "Bottom fade must hide when the body fits");

    // 14. Steam Gaming Mode at 1280x800: one calm card, no install steps, no waiting pill; RS scrolls from the footer.
    phase = "gaming-mode";
    await page.setViewportSize({ width: 1280, height: 800 });
    await startFresh({ motion: "off", search: "?gamingMode=1", onboarding: { status: "", step: "extension" } });
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await page.getByTestId("extension-gaming-mode-card").waitFor();
    check(!(await page.getByTestId("extension-pairing-pill").count()), "Gaming Mode must hide the waiting pill");
    check(!(await dialog().getByText("Chrome / Chromium / Edge", { exact: true }).count()), "Gaming Mode must hide the install steps");
    check(!(await dialog().getByRole("button", { name: "Open extension folder" }).count()), "Gaming Mode must hide Open extension folder");
    check(await dialog().getByRole("button", { name: "Next", exact: true }).isVisible(), "Gaming Mode footer must keep Next");
    check(!(await hintLabels()).includes("RS:Scroll"), "RS Scroll hint must hide when the body fits");
    // A paired extension still shows as paired.
    await page.evaluate(() => {
      window.pywebview = { api: { get_extension_pairing: async () => ({ success: true, paired: [{ id: "ABCD-12", browser: "Firefox", origin: "moz-extension://x" }] }) } };
      window.dispatchEvent(new Event("wlib-extension-pairing"));
    });
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="extension-pairing-pill"]')?.textContent?.includes("Paired with Firefox"),
    );
    await page.evaluate(() => { delete window.pywebview; });
    // Catalog is taller than the body in Gaming Mode: fade, RS hint, and RS scrolls the body with focus on the footer.
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await dialog().getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    await waitFade("bottom", true, "Bottom fade must show on Catalog in Gaming Mode");
    check((await hintLabels()).includes("RS:Scroll"), "RS Scroll hint must show while the body can scroll");
    await dialog().getByRole("button", { name: "Next", exact: true }).focus();
    for (let i = 0; i < 12; i++) await dispatch({ type: "scroll", x: 0, y: 1 });
    await page.waitForTimeout(150);
    check((await body.evaluate((el) => el.scrollTop)) > 0, "RS must scroll the onboarding body when focus is on the footer");

    // 15. Catalog with no local copy (held on a new install): the help never mentions deletion, On or Off.
    phase = "catalog-no-copy";
    await page.setViewportSize({ width: 1280, height: 820 });
    await startFresh({ motion: "off", catalogInstalled: false, onboarding: { status: "", step: "catalog" } });
    await page.getByRole("heading", { name: "Game catalog", exact: true }).waitFor();
    const noCopyHelp = await dialog().locator("#onboarding-catalog-help").textContent();
    check(noCopyHelp?.includes("When off, wLib does not download it.") && !/delet/i.test(noCopyHelp), `No-copy help must not mention deletion: ${noCopyHelp}`);
    await dialog().locator("label:has(#onboarding-catalog-enabled)").click();
    await dialog().getByText("Saved", { exact: true }).waitFor();
    await page.getByTestId("catalog-benefits").getByRole("heading", { name: "Without the catalog", exact: true }).waitFor();
    check(!(await dialog().getByText(/delet/i).count()), "No-copy Off state must not mention deletion");
    // Back on: the normal list returns.
    await dialog().locator("label:has(#onboarding-catalog-enabled)").click();
    await page.getByTestId("catalog-benefits").getByRole("heading", { name: "What you get", exact: true }).waitFor();

    // 16. Gaming Mode Done: no Pair now button, a plain note instead; Import games stays.
    phase = "gaming-mode-done";
    await startFresh({ motion: "off", search: "?gamingMode=1", onboarding: { status: "", step: "done" } });
    await page.getByRole("heading", { name: "Setup is finished", exact: true }).waitFor();
    check(!(await dialog().getByRole("button", { name: "Pair now" }).count()), "Gaming Mode Done must not offer Pair now");
    check((await dialog().locator('[data-summary-note="extension"]').textContent())?.trim() === "Later, in Desktop Mode", "Gaming Mode Done must say Later, in Desktop Mode");
    check(await dialog().getByRole("button", { name: "Import games", exact: true }).count() === 1, "Gaming Mode Done must keep Import games");

    check(errors.length === 0, `Page errors: ${errors.join("; ")}`);
    return "PASS: all onboarding steps, focus trap, extension queue/pairing, resume, Settings replay, import primary/Skip import/RB import, controller flow, scroll cue, Gaming Mode extension card, honest Done headline, rail not-set markers, Done row jumps, and the catalog On/Off list and help";
  } catch (error) {
    throw new Error(`${phase}: ${error}`);
  } finally {
    page.off("pageerror", onPageError);
    await page.evaluate((prev) => {
      delete window.pywebview;
      delete window.__finishImport;
      delete window.__mockPaired;
      delete window.__pairAnswer;
      for (const [k, v] of prev) {
        if (v === null) localStorage.removeItem(k);
        else localStorage.setItem(k, v);
      }
    }, previous);
    if (previousViewport) await page.setViewportSize(previousViewport);
    await page.goto(previousUrl);
    await page.reload();
  }
}
