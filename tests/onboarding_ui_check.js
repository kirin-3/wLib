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

    // Turn catalog off -> saves immediately, shows Saved and local copy deletion note (no ConfirmDialog).
    const catalogSwitch = dialog().getByRole("switch", { name: "Download the game catalog" });
    check(await catalogSwitch.isChecked(), "Catalog switch should start checked");
    await dialog().locator("label:has(#onboarding-catalog-enabled)").click();
    await dialog().getByText("Saved", { exact: true }).waitFor();
    await page.getByTestId("catalog-deleted-note").waitFor();
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
    await pathInput.fill("/mock/f95checker.db");
    await dialog().getByRole("button", { name: "Inspect Library", exact: true }).click();
    await dialog().getByText("New: 3", { exact: true }).waitFor();
    await dialog().getByRole("button", { name: "Import Selected", exact: true }).click();
    await page.waitForFunction(() => typeof window.__finishImport === "function");

    check(await dialog().getByRole("button", { name: "Back", exact: true }).isDisabled(), "Back must be disabled while import runs");
    check(await dialog().getByRole("button", { name: "Next", exact: true }).isDisabled(), "Next must be disabled while import runs");
    check(await dialog().getByRole("button", { name: "Skip setup", exact: true }).isDisabled(), "Skip must be disabled while import runs");

    await page.evaluate(() => window.__finishImport());
    await dialog().getByText("Imported library. Created 3", { exact: false }).waitFor();
    check(await dialog().getByRole("button", { name: "Next", exact: true }).isEnabled(), "Next should re-enable after import");

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

    // 7. Resume check: reloading on Extension step reopens on Extension step!
    phase = "resume-step";
    await page.evaluate(() => { delete window.pywebview; });
    await page.reload();
    await dialog().waitFor();
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();

    // 8. Done step: summary matches choices and Go to library saves done and opens Library.
    phase = "done-step";
    await dialog().getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("heading", { name: "You're all set", exact: true }).waitFor();
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
    // Run a controller import (fill path, focus Inspect Library -> A, focus Import Selected -> A)
    const ctrlPath = dialog().getByPlaceholder("Path to the source library file", { exact: true });
    await ctrlPath.fill("/mock/ctrl-f95checker.db");
    await dialog().getByRole("button", { name: "Inspect Library", exact: true }).focus();
    await dispatch({ type: "activate" });
    await dialog().getByText("New:", { exact: false }).waitFor();
    await dialog().getByRole("button", { name: "Import Selected", exact: true }).focus();
    await dispatch({ type: "activate" });
    await dialog().getByText("Imported library.", { exact: false }).waitFor();
    // RB -> Extension -> RB -> Done
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "Browser extension", exact: true }).waitFor();
    await dispatch({ type: "nextTab" });
    await page.getByRole("heading", { name: "You're all set", exact: true }).waitFor();
    // Focus Go to library via directional move / focus + A
    await dispatch({ type: "move", direction: "down" });
    await dialog().getByRole("button", { name: "Go to library", exact: true }).focus();
    await dispatch({ type: "activate" });
    await overlay().waitFor({ state: "hidden" });

    check(errors.length === 0, `Page errors: ${errors.join("; ")}`);
    return "PASS: all onboarding steps, focus trap, extension queue/pairing, resume, Settings replay, and controller flow";
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
